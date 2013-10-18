from google.appengine.ext import db
from google.appengine.api import users
import operator
import webapp2
import webapp2_extras.routes

import json
import types
import base64
import datetime

operators = {
    "==": operator.eq, "<": operator.lt, ">": operator.gt,
    "!=": operator.ne, "<=": operator.le, ">=": operator.ge
}


class ReverseReferenceProperty(list):
    pass


class LinkedReferenceProperty(db.ReferenceProperty):
    def __init__(self, collection_name, **kwargs):
        self.collection_name = collection_name
        super(LinkedReferenceProperty, self).__init__(**kwargs)

    def _fix_up(self, cls, code_name):
        super(LinkedReferenceProperty, self)._fix_up(cls, code_name)
        modelclass = db.Model._kind_map[self._kind]
        setattr(modelclass, self.collection_name,
                ReverseReferenceProperty((cls, self)))


def BuildRoute(baseurl, models, authenticate=users.get_current_user,
               authorize=None):
    if isinstance(models, types.ModuleType):
        models = [m for m in models.__dict__.values() if isinstance(
            m, db.model.MetaModel) and db.Model in m.__bases__]

    models = dict([(m.__name__, m) for m in models])

    def auth(instance, method, resource):
        if authorize is not None:
            user = authenticate()
            if not authorize(user, method, resource):
                instance.abort(403)

    class RESTHandler(webapp2.RequestHandler):
        # def abort(self, code, text=""):
        def abort(self, code, *args, **kwargs):
            webapp2.RequestHandler.abort(self, code, kwargs.get('text', ''),
                                         body_template="${detail}")

        def selectModel(self, classname):
            if classname not in models: self.abort(404)
            return models[classname]

        def buildURI(self, target, collection=None):
            if isinstance(target, db.Key):
                if collection:
                    return webapp2.uri_for("rest-model-collection",
                                           modelname=target.kind(),
                                           itemid=target.id(),
                                           collectionname=collection,
                                           _full=True)
                else:
                    return webapp2.uri_for("rest-model-item",
                                           modelname=target.kind(),
                                           itemid=target.id(), _full=True)
            else:
                return webapp2.uri_for("rest-model-list",
                                       modelname=target.__name__, _full=True)

        def encode(self, item):
            properties = {}
            for fieldname in item.to_dict():
                field = getattr(item, fieldname)
                field_class = item._properties[fieldname].__class__
                if field is None:
                    pass
                elif field_class is db.BlobProperty:
                    field = base64.b64decode(item)
                elif field_class is db.DateTimeProperty:
                    field = field.strftime("%Y %b %d %H:%M:%S")
                elif field_class is db.DateProperty:
                    field = field.strftime("%Y %b %d")
                elif field_class is db.TimeProperty:
                    field = field.strftime("%H:%M:%S")
                properties[fieldname] = field
            for key, val in item.__class__.__dict__.iteritems():
                if isinstance(val, ReverseReferenceProperty):
                    properties[key] = self.buildURI(item.key, key)
            return {
                "href": self.buildURI(item.key),
                "key": item.key.id(),
                "class": item.__class__.__name__,
                "properties": properties
            }

        def putItem(self, item, put_data):
            badfields = []
            for fieldname, field in put_data.iteritems():
                if fieldname in item._properties:
                    field_class = item._properties[fieldname].__class__
                    if field_class is db.IntegerProperty:
                        if type(field) is not int:
                            badfields.append(fieldname)
                            continue
                    elif field_class is db.FloatProperty:
                        if type(field) is not float:
                            badfields.append(fieldname)
                            continue
                    elif field_class is db.BooleanProperty:
                        if type(field) is not bool:
                            badfields.append(fieldname)
                            continue
                    elif field_class is db.StringProperty:
                        if len(str(field)) > 500:
                            badfields.append(fieldname)
                            continue
                    elif field_class is db.BlobProperty:
                        if type(field) is not str:
                            badfields.append(fieldname)
                            continue
                        try:
                            field = base64.b64decode(field)
                        except ValueError as e:
                            badfields.append(fieldname)
                            continue
                    elif field_class is db.DateTimeProperty:
                        try:
                            field = datetime.datetime.strptime(
                                field, "%Y %b %d %H:%M:%S")
                        except ValueError as e:
                            badfields.append(fieldname)
                            continue
                    elif field_class is db.DateProperty:
                        try:
                            field = datetime.datetime.strptime(
                                field, "%Y %b %d").date()
                        except ValueError as e:
                            badfields.append(fieldname)
                            continue
                    elif field_class is db.TimeProperty:
                        try:
                            field = datetime.datetime.strptime(
                                field, "%H:%M:%S").time()
                        except ValueError as e:
                            badfields.append(fieldname)
                            continue
                    setattr(item, fieldname, field)
            if len(badfields) > 0:
                self.abort(400, "Invalid field values: {0}".format(
                    ", ".join(badfields)))
            item.put()
            return item

        def postItem(self, model, post_data):
            kwargs = {}
            if "key" in post_data:
                kwargs["id"] = post_data["key"]
                del (post_data["key"])
            return self.putItem(model(**kwargs), post_data)

        def deleteItems(self, model, itemlist):
            db.delete([i.key for i in itemlist])

        def fieldFromString(self, modelname, fieldname):
            model = self.selectModel(modelname)
            field = None
            try:
                field = getattr(model, fieldname)
            except AttributeError:
                self.abort(400, "\"{0}\" is not a valid field in {1}".format(
                    fieldname, modelname))
            return field

        def filterQueryFromString(self, query, filterstring):
            parts = filterstring.split(" ", 2)
            if len(parts) != 3:
                return query
            fieldname, op, value = parts
            field = self.fieldFromString(query.kind, fieldname)
            if fieldname == "key":
                if value.isdigit(): value = int(value)
                # value = db.Key(query.kind, value)
                value = db.Key(query.kind)
            if op in operators:
                query = query.filter(operators[op](field, value))
            elif op == "IN":
                query = query.filter(getattr(field, "IN")(value.split(",")))
            else:
                self.abort(400, "Bad operator: {0}".format(op))
            return query

        def getCollection(self, query):
            filters = self.request.get_all("filter")
            sorts = [val or "__key__" for val in self.request.get_all("sort")]
            limit = self.request.get("limit").isdigit() and int(
                self.request.get("limit")) or 5
            offset = self.request.get("offset").isdigit() and int(
                self.request.get("offset")) or 0
            for f in filters: query = self.filterQueryFromString(query, f)
            for s in sorts: query = query.order(
                self.fieldFromString(query.kind, s))
            results = query.fetch(limit, offset=offset)
            return [self.encode(item) for item in results]

        def deleteCollection(self, model, itemlist):
            self.deleteItems(model, itemlist)

        def buildCollectionQuery(self, item, collectionname):
            model, field = getattr(item, collectionname)
            return model.query(field == item.key)

    class RESTBaseHandler(RESTHandler):
        def get(self):
            auth(self, "get", None)
            site_meta = {"resources": dict(
                [(name, self.buildURI(model)) for name, model in
                 models.iteritems()])}
            self.response.write(json.dumps(site_meta))

    class RESTModelListHandler(RESTHandler):
        def _get(self, modelname):
            model = self.selectModel(modelname)
            return model, self.getCollection(model.query())

        def get(self, modelname):
            itemlist = self.getCollection(self.selectModel(modelname).query())
            auth(self, "get", itemlist)
            self.response.write(json.dumps({'results': itemlist}))

        def delete(self, modelname):
            model, itemlist = self._get(modelname)
            auth(self, "delete", itemlist)
            self.deleteCollection(model, itemlist)
            self.abort(204)

        def post(self, modelname):
            post_data = json.loads(self.request.body)
            model = self.selectModel(modelname)
            auth(self, "post", model)
            item = self.postItem(model, post_data)
            self.response.set_status(303)
            self.response.headers['Location'] = self.buildURI(item.key)

    class RESTModelItemHandler(RESTHandler):
        def get(self, modelname, itemid):
            model = self.selectModel(modelname)
            if itemid.isdigit(): itemid = int(itemid)
            item = model.get_by_id(itemid)
            auth(self, "get", item)
            if not item: self.abort(404)
            self.response.write(json.dumps(self.encode(item)))

        def delete(self, modelname, itemid):
            model = self.selectModel(modelname)
            if itemid.isdigit(): itemid = int(itemid)
            item = model.get_by_id(itemid)
            auth(self, "delete", item)
            if not item: self.abort(404)
            self.deleteItems(model, [item])
            self.abort(204)

        def put(self, modelname, itemid):
            put_data = json.loads(self.request.body)
            model = self.selectModel(modelname)
            if itemid.isdigit(): itemid = int(itemid)
            item = model.get_by_id(itemid)
            auth(self, "put", item)
            self.putItem(item, put_data)
            self.response.set_status(303)
            self.response.headers['Location'] = self.buildURI(item.key)

    class RESTModelCollectionHandler(RESTHandler):
        def _get(self, modelname, itemid, collectionname):
            model = self.selectModel(modelname)
            if itemid.isdigit(): itemid = int(itemid)
            item = model.get_by_id(itemid)
            auth(self, "get", item)
            if not item: self.abort(404)
            try:
                return model, self.getCollection(
                    self.buildCollectionQuery(item, collectionname))
            except AttributeError:
                self.abort(404)

        def get(self, modelname, itemid, collectionname):
            model, itemlist = self._get(modelname, itemid, collectionname)
            auth(self, "get", itemlist)
            self.response.write(json.dumps({'results': itemlist}))

        def delete(self, modelname, itemid, collectionname):
            model, itemlist = self._get(modelname, itemid, collectionname)
            auth(self, "delete", itemlist)
            self.deleteCollection(model, itemlist)
            self.abort(204)

        def post(self):
            # Add the item (specified by id) to the collection
            pass

    return webapp2_extras.routes.PathPrefixRoute(baseurl, [
        webapp2_extras.routes.RedirectRoute(
            '/', RESTBaseHandler, 'rest-base', strict_slash=True),
        webapp2_extras.routes.RedirectRoute(
            '/<modelname>/', RESTModelListHandler, 'rest-model-list',
            strict_slash=True),
        webapp2_extras.routes.RedirectRoute(
            '/<modelname>/<itemid>', RESTModelItemHandler, 'rest-model-item',
            strict_slash=True),
        webapp2_extras.routes.RedirectRoute(
            '/<modelname>/<itemid>/<collectionname>/',
            RESTModelCollectionHandler, 'rest-model-collection',
            strict_slash=True),
    ])