# restfulgae

This lib can be used to expose your Google App Engine Datastore models via an easy-to-use JSON REST API.

Just include the REST folder in your project, and plug in your models. Restfulgae will build a Route object for webapp2 for you. It will even inspect an entire module for models, and include them all.

Memcached support is included, and you can specify a memcache key prefix using the memcache_prefix keyword arg of REST.BuildRoute.

*Here, you can import all models from the "mymodels" module, and exposes them via an API*

```python
import mymodels, webapp2, restfulgae

application = webapp2.WSGIApplication([
    # your app's other routes can go here
    restfulgae.BuildRoute("/api", mymodels)
],debug=True)
```

*Or just list specific models to be exposed*

```python
import mymodels, webapp2, restfulgae

application = webapp2.WSGIApplication([
    # your app's other routes can go here
    restfulgae.BuildRoute("/api", [mymodels.Foo, mymodels.Bar, mymodels.Baz])
],debug=True)
```
