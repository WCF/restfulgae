from google.appengine.ext import db
from google.appengine.api import memcache
import webapp2
import webapp2_extras.routes

import json
import types
import datetime

# TODO:
#   add support for authentication / authorization
#   add support for write operations (create, update, delete)
#   check the accept header, and support HTML output (GET only)
#   break fieldtype-specific (de-)serialization (HTML and JSON) support into it's own file
#   support all stock fieldtypes (these will probably be added as I use them in an app)

def BuildRoute(baseurl, models, memcache_prefix="REST_"):
    if isinstance(models, types.ModuleType):
        # if models is a module, find all classes in it that inherit from db.Model, and use them
        models = [m for m in models.__dict__.values() if isinstance(m, db.PropertiedClass) and db.Model in m.__bases__]

    models = dict([(m.__name__, m) for m in models])
    
    class RESTHandler(webapp2.RequestHandler):
        def selectModel(self, classname):
            if classname not in models: self.abort(404)
            return models[classname]
        
        def buildURI(self, target, collection=None):
            if isinstance(target, db.Model):
                if collection:
                    return webapp2.uri_for("rest-model-collection", modelname=target.__class__.__name__, itemid=target.key().id_or_name(), collectionname=collection, _full=True)
                else:
                    return webapp2.uri_for("rest-model-item", modelname=target.__class__.__name__, itemid=target.key().id_or_name(), _full=True)
            else:
                return webapp2.uri_for("rest-model-list", modelname=target.__name__, _full=True)
        
        def encode(self, item):
            properties = {}
            for fieldname in item.fields():
                field = getattr(item, fieldname)
                if isinstance(field, datetime.datetime):
                    field = field.ctime().split()
                if isinstance(field, db.Model):
                    field = self.buildURI(field)
                properties[fieldname] = field
            for key, val in item.__class__.__dict__.iteritems():
                if isinstance(val, db._ReverseReferenceProperty):
                    properties[key] = self.buildURI(item, key)
            return {
                "href": self.buildURI(item),
                "key": item.key().id_or_name(),
                "class": item.__class__.__name__,
                "properties": properties
            }

        def getItems(self, model, keys):
            data = memcache.get_multi(keys, "%s%s_" % (memcache_prefix, model.__name__))
            if data is None:
                data = {}
            for key in keys:
                if key not in data:
                    if key.isdigit():
                        item = model.get_by_id(int(key))
                    else:
                        item = model.get_by_key_name(key)
                    memcache.set("%s%s_%s" % (memcache_prefix, model.__name__, key), item)
                    data[key] = item
            return data

        def getCollection(self, query):
            collection = memcache.get("%sCOLLECTION_%s" % (memcache_prefix, self.request.path_qs))
            if collection is not None:
                results = self.getItems(collection['model'], collection['keys']).values()
            else:
                filters = [len(val.split(" ", 2)) == 3 and val.split(" ", 2) or None for val in self.request.get_all("filter")]
                sorts = [val or "__key__" for val in self.request.get_all("sort")]
                limit = self.request.get("limit").isdigit() and int(self.request.get("limit")) or 5
                offset = self.request.get("offset").isdigit() and int(self.request.get("offset")) or 0
                try:
                    for f in filters: query = query.filter("%s %s" % tuple(f[:2]), f[2])
                    for s in sorts: query = query.order(s)
                    results = query.fetch(limit, offset)
                except db.PropertyError:
                    self.abort(400)
                collection = {
                    'model': query._model_class,
                    'keys': [str(item.key().id_or_name()) for item in results]
                }
                memcache.set("%sCOLLECTION_%s" % (memcache_prefix, self.request.path_qs), collection)
                for item in results:
                    memcache.set_multi(dict([(str(item.key().id_or_name()), item) for item in results]), key_prefix="%s%s_" % (memcache_prefix, query._model_class.__name__))
            return [self.encode(item) for item in results]

    class RESTBaseHandler(RESTHandler):
        def get(self):
            site_meta = {"resources": dict([(name, self.buildURI(model)) for name, model in models.iteritems()])}
            self.response.write(json.dumps(site_meta))
            
    class RESTModelListHandler(RESTHandler):
        def get(self, modelname):
            itemlist = self.getCollection(self.selectModel(modelname).all())
            self.response.write(json.dumps({'results': itemlist}))

    class RESTModelItemHandler(RESTHandler):
        def get(self, modelname, itemid):
            model = self.selectModel(modelname)
            item = self.getItems(model, [itemid])[itemid]
            if not item: self.abort(404)
            self.response.write(json.dumps(self.encode(item)))

    class RESTModelCollectionHandler(RESTHandler):
        def get(self, modelname, itemid, collectionname):
            model = self.selectModel(modelname)
            item = self.getItems(model, [itemid])[itemid]
            if not item: self.abort(404)
            try:
                itemlist = self.getCollection(getattr(item, collectionname))
            except AttributeError:
                self.abort(404)
            self.response.write(json.dumps({'results': itemlist}))
    
    return webapp2_extras.routes.PathPrefixRoute(baseurl, [
        webapp2_extras.routes.RedirectRoute('/', RESTBaseHandler, 'rest-base', strict_slash=True),
        webapp2_extras.routes.RedirectRoute('/<modelname>/', RESTModelListHandler, 'rest-model-list', strict_slash=True),
        webapp2_extras.routes.RedirectRoute('/<modelname>/<itemid>', RESTModelItemHandler, 'rest-model-item', strict_slash=True),
        webapp2_extras.routes.RedirectRoute('/<modelname>/<itemid>/<collectionname>/', RESTModelCollectionHandler, 'rest-model-collection', strict_slash=True),
    ])
