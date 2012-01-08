See http://wiki.github.com/k7d/approcket/

Installation on AppEngine
=========================

You need to put  `rocket.py` somewhere into your application. E.g

    mkdir lib
    git sublodule add https://github.com/k7d/approcket.git lib/approcket
    echo "import os.path" > lib/__init__.py
    echo "import site" >> lib/__init__.py
    echo "site.addsitedir(os.path.dirname(__file__))" >> lib/__init__.py
    echo "./approcket" >> lib/submodules.pth

Then add it to your `app.yaml` file, right after the line `handlers:`

    - url: /rocket/.*
      script: lib/approcket/rocket/handler.py

If you are on Python 2.7 try something like this:

    - url: /rocket/.*
      script: rocket.handler.application

Generate a secret key and add it to `appengine_config.py`
    
    echo "approcket_SECRET_KEY = '`dd if=/dev/urandom count=2000 | shasum | cut -d ' ' -f 1 | tail -n 1`'" >> appengine_config.py

Now your application is ready to deploy.

If you want to make absolutely sure that no external process modifies your datastore set `approcket_READONLY = True` in `appengine_config.py`.


Checking you models
-------------------

Your models need an automatically timestamped files like this:

    timestamp = db.DateTimeProperty(auto_now=True, indexed=True)

If you have created the field with `indexed=False` approcket can not work.
