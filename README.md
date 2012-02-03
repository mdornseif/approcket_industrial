See http://wiki.github.com/k7d/approcket/

Installation on AppEngine
=========================

You need to put  `rocket.py` somewhere into your application. E.g

    mkdir lib
    git sublodule add https://github.com/mdornseif/approcket_industrial.git lib/approcket_industrial
    echo "import os.path" > lib/__init__.py
    echo "import site" >> lib/__init__.py
    echo "site.addsitedir(os.path.dirname(__file__))" >> lib/__init__.py
    echo "./approcket_industrial" >> lib/submodules.pth

Then add it to your `app.yaml` file, right after the line `handlers:`

    - url: /rocket/.*
      script: lib/approcket_industrial/rocket/handler.py

If you are on Python 2.7 try something like this:

    - url: /rocket/.*
      script: rocket.handler.application

Generate a secret key and add it to `appengine_config.py`
    
    echo "approcket_SECRET_KEY = '`dd if=/dev/urandom count=2000 | shasum | cut -d ' ' -f 1 | tail -n 1`'" >> appengine_config.py

Now your application is ready to deploy.


Checking you models
-------------------

Your models need an automatically timestamped files like this:

    timestamp = db.DateTimeProperty(auto_now=True, indexed=True)

If you have created the field with `indexed=False` approcket can not work.


Starting Replication
-------------------

Execute something like this on your database server:

    (cd $BASEDIR/approcket_industrial/rocket ; \
        ./replicator.py \
        --database_name=mydb \
        --database_user=mydbuser \
        --database_password=ph7etaXii5Ajek8a \
        --rocketurl=http://myapp.appspot.com/rocket \
        -s 4eabfa74e5d1c9c47e961156f0517205b4486b74 )


History
=======

The fantatic approcket was originaly concived by Kaspars Dancis and [published on github][1]. `approcket_industrial` is a fork removing features and adding robustness and code which is easier to audit. `approcket_industrial` saves resources by using datastore cursors. It ensures that data only flows in a single direction (from AppEngine to mySQL) and can handle characters illegal in XML. It also ensures keymaterial is kept out of version control.

[1]: https://github.com/k7d/approcket
