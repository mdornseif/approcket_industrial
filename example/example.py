import logging
import os
import random
from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.ext import db
from google.appengine.ext.webapp import template
from google.appengine.api import urlfetch


class NotAComment(db.Model):
    timestamp = db.DateTimeProperty(auto_now=True)
    bool1 = db.BooleanProperty()
    bool2 = db.BooleanProperty()
    i1 = db.IntegerProperty()
    image1 = db.BlobProperty()


class Comment(db.Model):
    timestamp = db.DateTimeProperty(auto_now=True)
    content = db.StringProperty(multiline=True)
    list1 = db.StringListProperty()
    list2 = db.StringListProperty()
    yeah = db.ReferenceProperty(NotAComment)


class Comments(webapp.RequestHandler):
    def get(self):
        comments = Comment.all()
        path = os.path.join(os.path.dirname(__file__), 'index.html')
        self.response.out.write(template.render(path, {'comments': comments}))

    def post(self):
        yeah_key = "YEAH%d" % random.Random().randint(1, 3)
        yeah = NotAComment.get_by_key_name(yeah_key)
        if not yeah:
            imgurl = "http://groups.google.com/group/approcket/icon?v=1&hl=en"
            yeah = NotAComment(
                key_name=yeah_key,
                bool1=False,
                bool2=True,
                i1=random.Random().randint(0, 1234567),
                image1=urlfetch.fetch(imgurl).content,
            ).put()

        Comment(
            content=self.request.get('content'),
            list1=self.request.get_all('list1'),
            list2=self.request.get_all('list2'),
            yeah=yeah,
        ).put()

        return self.get()


class Images(webapp.RequestHandler):
    def get(self):
        key_name = self.request.get('key_name')
        yeah = NotAComment.get_by_key_name(key_name)
        self.response.headers['Content-Type'] = 'image/gif'
        self.response.out.write(yeah.image1)

application = webapp.WSGIApplication([('/', Comments), ('/images', Images)],
                                      debug=True)


def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()
