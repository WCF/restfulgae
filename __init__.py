from google.appengine.ext import ndb
from google.appengine.api import users
import operator
import webapp2
import webapp2_extras.routes

import json
import types
import datetime

operators = {"==": operator.eq, "<": operator.lt, "<=": operator.le , ">": operator.gt , ">=": operator.ge , "!=": operator.ne}

class ReverseReferenceProperty(list):
    pass

class LinkedKeyProperty(ndb.KeyProperty):
    def __init__(self, collection_name, **kwargs):
        self.collection_name = collection_name
        super(LinkedKeyProperty, self).__init__(**kwargs)
    def _fix_up(self, cls, code_name):
        super(LinkedKeyProperty, self)._fix_up(cls, code_name)
        modelclass = ndb.Model._kind_map[self._kind]
        setattr(modelclass, self.collection_name, ReverseReferenceProperty((cls, self)))

def BuildRoute(baseurl, models, authenticate=users.get_current_user, authorize=None):
    if isinstance(models, types.ModuleType):
        models = [m for m in models.__dict__.values() if isinstance(m, ndb.model.MetaModel) and ndb.Model in m.__bases__]

    models = dict([(m.__name__, m) for m in models])
    
    def auth(method, resource):
        if authorize is not None:
            user = authenticate()
            if not authorize(user, method, resource):
                self.abort(403)
    
    class RESTHandler(webapp2.RequestHandler):
        def selectModel(self, classname):
            if classname not in models: self.abort(404)
            return models[classname]
        
        def buildURI(self, target, collection=None):
            if isinstance(target, ndb.Model):
                if collection:
                    return webapp2.uri_for("rest-model-collection", modelname=target.__class__.__name__, itemid=target.key.id(), collectionname=collection, _full=True)
                else:
                    return webapp2.uri_for("rest-model-item", modelname=target.__class__.__name__, itemid=target.key.id(), _full=True)
            else:
                return webapp2.uri_for("rest-model-list", modelname=target.__name__, _full=True)
        
        def encode(self, item):
            properties = {}
            for fieldname in item.to_dict():
                field = getattr(item, fieldname)
                if isinstance(field, datetime.datetime):
                    field = field.ctime().split()
                if isinstance(field, ndb.Key):
                    field = self.buildURI(field.get())
                properties[fieldname] = field
            for key, val in item.__class__.__dict__.iteritems():
                if isinstance(val, ReverseReferenceProperty):
                    properties[key] = self.buildURI(item, key)
            return {
                "href": self.buildURI(item),
                "key": item.key.id(),
                "class": item.__class__.__name__,
                "properties": properties
            }
        
        def postItem(self, model, post_data):
            return putItem(model(), post_data)
        
        def putItem(self, item, put_data):
            for k, v in put_data.iteritems():
                if k in item.to_dict():
                    setattr(item, k, v)
            item.put()
        
        def deleteItems(self, model, itemlist):
            ndb.delete_multi([i.key for i in itemlist])
        
        def fieldFromString(self, modelname, fieldname):
            model = self.selectModel(modelname)
            try:
                field = getattr(model, fieldname)
            except AttributeError:
                self.abort(400)
            return field
        
        def filterQueryFromString(self, query, filterstring):
            parts = filterstring.split(" ", 2)
            if len(parts) != 3:
                return query
            fieldname, op, value = parts
            field = self.fieldFromString(query.kind, fieldname)
            if op in operators:
                query = query.filter(operators[op](field, value))
            elif op == "IN":
                query = query.filter(getattr(field, "IN")(value.split(",")))
            else:
                self.abort(400)
            return query
        
        def getCollection(self, query):
            filters = self.request.get_all("filter")
            sorts = [val or "__key__" for val in self.request.get_all("sort")]
            limit = self.request.get("limit").isdigit() and int(self.request.get("limit")) or 5
            offset = self.request.get("offset").isdigit() and int(self.request.get("offset")) or 0
            for f in filters: query = self.filterQueryFromString(query, f)
            for s in sorts: query = query.order(self.fieldFromString(query.kind, s))
            results = query.fetch(limit, offset=offset)
            return [self.encode(item) for item in results]
        
        def deleteCollection(self, model, itemlist):
            self.deleteItems(model, itemlist)

        def buildCollectionQuery(self, item, collectionname):
            model, field = getattr(item, collectionname)
            return model.query(field == item.key)
    
    class RESTBaseHandler(RESTHandler):
        def get(self):
            auth("get", None)
            site_meta = {"resources": dict([(name, self.buildURI(model)) for name, model in models.iteritems()])}
            self.response.write(json.dumps(site_meta))
    
    class RESTModelListHandler(RESTHandler):
        def _get(self, modelname):
            model = self.selectModel(modelname)
            return model, self.getCollection(model.query())
        def get(self, modelname):
            itemlist = self.getCollection(self.selectModel(modelname).query())
            auth("get", itemlist)
            self.response.write(json.dumps({'results': itemlist}))
        def delete(self, modelname):
            model, itemlist = self._get(modelname)
            auth("delete", itemlist)
            self.deleteCollection(model, itemlist)
            self.abort(204)
        def post(self, modelname):
            post_data = json.loads(self.request.body)
            model = self.selectModel(modelname)
            auth("post", model)
            item = self.postItem(model, post_data)
            self.redirect(self.buildURI(item))
    
    class RESTModelItemHandler(RESTHandler):
        def get(self, modelname, itemid):
            model = self.selectModel(modelname)
            if itemid.isdigit(): itemid = int(itemid)
            item = model.get_by_id(itemid)
            auth("get", item)
            if not item: self.abort(404, "%s %s" %(modelname, itemid))
            self.response.write(json.dumps(self.encode(item)))
        def delete(self, modelname, itemid):
            model, item = self._get(modelname, itemid)
            auth("delete", item)
            if not item: self.abort(404)
            self.deleteItems(model, [item])
            self.abort(204)
        def put(self, modelname, itemid):
            put_data = json.loads(self.request.body)
            model = self.selectModel(modelname)
            if itemid.isdigit(): itemid = int(itemid)
            item = model.get_by_id(itemid)
            auth("put", item)
            self.putItem(item, put_data)
            self.redirect(self.buildURI(item))
    
    class RESTModelCollectionHandler(RESTHandler):
        def _get(self, modelname, itemid, collectionname):
            model = self.selectModel(modelname)
            if itemid.isdigit(): itemid = int(itemid)
            item = model.get_by_id(itemid)
            auth("get", item)
            if not item: self.abort(404)
            try:
                return model, self.getCollection(self.buildCollectionQuery(item, collectionname))
            except AttributeError:
                self.abort(404)
        def get(self, modelname, itemid, collectionname):
            model, itemlist = self._get(modelname, itemid, collectionname)
            auth("get", itemlist)
            self.response.write(json.dumps({'results': itemlist}))
        def delete(self, modelname, itemid, collectionname):
            model, itemlist = self._get(modelname, itemid, collectionname)
            auth("delete", itemlist)
            self.deleteCollection(model, itemlist)
            self.abort(204)
        def post(self):
            # Add the item (specified by id) to the collection
            pass
    
    return webapp2_extras.routes.PathPrefixRoute(baseurl, [
        webapp2_extras.routes.RedirectRoute('/', RESTBaseHandler, 'rest-base', strict_slash=True),
        webapp2_extras.routes.RedirectRoute('/<modelname>/', RESTModelListHandler, 'rest-model-list', strict_slash=True),
        webapp2_extras.routes.RedirectRoute('/<modelname>/<itemid>', RESTModelItemHandler, 'rest-model-item', strict_slash=True),
        webapp2_extras.routes.RedirectRoute('/<modelname>/<itemid>/<collectionname>/', RESTModelCollectionHandler, 'rest-model-collection', strict_slash=True),
    ])
