#!/usr/bin/env python

import cgi
import datetime

from google.appengine.api import memcache
from google.appengine.api import users
from google.appengine.ext import webapp
from google.appengine.ext.webapp import util
from google.appengine.ext import db
from google.appengine.api import taskqueue

import os
from google.appengine.ext.webapp import template
import logging

import sys
sys.path.insert(0, 'tweepy.zip')
import tweepy

try:
    import settings # Assumed to be in the same directory.
except ImportError:
    import sys
    sys.stderr.write("Error: Can't find the file 'settings.py'.")
    sys.exit(1)

NS_HANDLE = 'twitter_handle_'
HANDLE_EXP = 24 * 60 * 60

NS_TOKEN = 'otoken_'
TOKEN_EXP = 60 * 60

PATH_CALLBACK = '/user/callback'
PATH_HOME = '/user/home'

class Account(db.Model):
    owner = db.UserProperty(required=True)
    twitter_id = db.IntegerProperty(required=True, indexed=True)
    active = db.BooleanProperty(default=False, indexed=True)
    oauth_key = db.StringProperty()
    oauth_secret = db.StringProperty()
    hours = db.IntegerProperty(default = 48)
    created_on = db.DateTimeProperty(auto_now_add=True)
    activated_on = db.DateTimeProperty(indexed=False)
    activated_status_id = db.IntegerProperty(default = 0)
    latest_screen_name = db.StringProperty(indexed=False)

    def display_name(self):
        id = NS_HANDLE + str(self.twitter_id)
        name = memcache.get(id)
        if name is not None:
            return name
        
        try:
            api = tweepy.API()
            user = api.get_user(user_id = self.twitter_id)
            if user is None:
                raise LookupError                
            name = user.screen_name
            
            # Update the screen name
            self.latest_screen_name = user.screen_name
            self.save()
            memcache.set(id, name, HANDLE_EXP)
        except Exception:
            name = self.latest_screen_name
        return name

class MainPage(webapp.RequestHandler):
    def get(self):
        current_user = users.get_current_user()
        if current_user:
            url = users.create_logout_url(self.request.uri)
#            account = Account(owner = users.get_current_user(), twitter_id = 1234)
#            account.active = False
#            account.hours = 24
#            account.latest_screen_name = 'Early_1234'
#            account.save()
#            
#            account = Account(owner = users.get_current_user(), twitter_id = 2345)
#            account.active = False
#            account.hours = 36
#            account.latest_screen_name = 'Later_2345'
#            account.save()
        else:
            url = users.create_login_url(self.request.uri)

        template_values = {
            'current_user': current_user,
            'url': url,
            }

        path = os.path.join(os.path.dirname(__file__), 'templates/index.html')
        self.response.out.write(template.render(path, template_values))

class HomePage(webapp.RequestHandler):
    def get(self):
                
        if not users.get_current_user():
            self.redirect('/')
            return

        q = Account.all()
        q.filter("owner =", users.get_current_user())
        accounts = q.fetch(10)
        
        template_values = {                           
            'current_user': users.get_current_user(),
            'url': users.create_logout_url("/"),
            'accounts': accounts,
            'allow_add': len(accounts) < 10,
            }

        path = os.path.join(os.path.dirname(__file__), 'templates/home.html')
        self.response.out.write(template.render(path, template_values))

class EditPage(webapp.RequestHandler):
    def get(self):
        
        current_user = users.get_current_user()        
        if not current_user:
            self.redirect('/')
            return

        try:
            twitter_id = int(self.request.get('id'))
        except ValueError:
            self.redirect(PATH_HOME)
            return
        
        gql = db.GqlQuery("SELECT * FROM Account WHERE twitter_id = :1", twitter_id)
        account = gql.get()
        if account is None or account.owner != current_user:
            self.redirect(PATH_HOME)
            return
        
        template_values = {
            'current_user': current_user,
            'url': users.create_logout_url("/"),
            'account': account,
            }
        path = os.path.join(os.path.dirname(__file__), 'templates/edit.html')
        self.response.out.write(template.render(path, template_values))
        
class Update(webapp.RequestHandler):
    def post(self, id_str):
        current_user = users.get_current_user()        
        if not current_user:
            self.redirect('/')
            return

        try:
            twitter_id = int(id_str)
        except ValueError:
            self.redirect(PATH_HOME)
            return
        
        gql = db.GqlQuery("SELECT * FROM Account WHERE twitter_id = :1", twitter_id)
        account = gql.get()
        if account is None or account.owner != current_user:
            self.redirect(PATH_HOME)
            return

        account.hours = int(self.request.get('input_hours'))
        
        active = bool(self.request.get('input_active'))
        if active != account.active:
            if active:
                status_id = 0
                try:
                    auth = tweepy.OAuthHandler(settings.CONSUMER_KEY, settings.CONSUMER_SECRET)
                    auth.set_access_token(account.oauth_key, account.oauth_secret)
                    api = tweepy.API(auth)
                    for status in tweepy.Cursor(api.user_timeline).items(1):
                        logging.warning("> " + account.display_name() + " said: " + status.text)
                        logging.warning("> StatusId: " + str(status.id))
                        status_id = status.id
                except Exception:
                    logging.error('Unable to find the most recent status id for twitter_id:' + str(account.twitter_id))
                
                account.active = True
                account.activated_on = datetime.datetime.utcnow()
                account.activated_status_id = status_id
            else:
                account.active = False
                account.activated_on = None
                account.activated_status_id = 0
        
        account.save()
        self.redirect(PATH_HOME)
                
class AddPage(webapp.RequestHandler):
    def get(self):
        current_user = users.get_current_user()        
        if not current_user:
            self.redirect('/')
            return
        
        # Build a new oauth handler and display authorization url to user.
        callback = self.request.host_url + PATH_CALLBACK
        auth = tweepy.OAuthHandler(settings.CONSUMER_KEY, settings.CONSUMER_SECRET, callback)
        try:
            auth_url = auth.get_authorization_url()
            id = NS_TOKEN + auth.request_token.key
            memcache.set(id, auth.request_token.secret, TOKEN_EXP)
            self.redirect(auth_url)
        except Exception:
            self.redirect(PATH_HOME)            
      
class CallbackPage(webapp.RequestHandler):
    def get(self):
        current_user = users.get_current_user()        
        if not current_user:
            self.redirect('/')
            return

        oauth_token = self.request.get("oauth_token", None)
        oauth_verifier = self.request.get("oauth_verifier", None)
        if oauth_token is None:
            # Invalid request!
            self.redirect(PATH_HOME)
            return
        
        id = NS_TOKEN + oauth_token
        oauth_secret = memcache.get(id)
        if oauth_secret == None:
            # Token not found
            self.redirect(PATH_HOME)
            return
        
        # Rebuild the auth handler
        auth = tweepy.OAuthHandler(settings.CONSUMER_KEY, settings.CONSUMER_SECRET)
        auth.set_request_token(oauth_token, oauth_secret)
        
        # Fetch the access token
        try:
            auth.get_access_token(oauth_verifier)
        except tweepy.TweepError, e:
            self.redirect(PATH_HOME)
            return

        api = tweepy.API(auth)
        user = api.me()        
        existing = Account.gql("WHERE twitter_id=:key", key=user.id).get()
        if existing is not None:
            existing.oauth_key = auth.access_token.key
            existing.oauth_secret = auth.access_token.secret
            existing.latest_screen_name = user.screen_name
            existing.save()
        else:
            fresh = Account(owner=current_user, twitter_id = user.id)
            fresh.oauth_key = auth.access_token.key
            fresh.oauth_secret = auth.access_token.secret
            fresh.active = False
            fresh.latest_screen_name = user.screen_name
            fresh.save()
        
        self.redirect(PATH_HOME)
        
class CleaningDelegator(webapp.RequestHandler):
    def get(self):
        accounts = Account.all().filter('active =', True)  
        account_total = 0      
        for account in accounts:
            account_total += 1
            taskqueue.add(url='/admin/worker',params={"id": str(account.twitter_id)}, method='GET')
            
        status = "Total Accounts: " + str(account_total) + " - " + str(datetime.datetime.now())
        auth = tweepy.OAuthHandler(settings.CONSUMER_KEY, settings.CONSUMER_SECRET)
        auth.set_access_token(settings.MONITOR_KEY, settings.MONITOR_SECRET)
        api = tweepy.API(auth)                    
        api.update_status(status)
        logging.info(status)
                                                  
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.out.write('CleaningDelegator Done!')
        
class CleaningWorker(webapp.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        self.response.out.write('CleaningWorker Done!')
        id_str = self.request.get("id", None)
        try:
            id = int(id_str)
        except ValueError:
            logging.warning("Could not do any cleaning for worker: " + id_str)
            return
        accounts = db.GqlQuery('SELECT * FROM Account WHERE twitter_id = :1', id)
        account = accounts.get()
        if account is None:
            logging.warning("Counld not find any account for id: " + id_str) 
            return       
        if account.oauth_key is None or account.oauth_secret is None:
            logging.warning("Skipping processing for account w/o OAuth Credentials. ID: " + id_str)
            return        
        if account.active != True:
            logging.warning("> Skipping processing for account that is inactive. ID: " + id_str)
            return
        if account.hours <= 0:
            logging.warning("> Skipping processing for account that has an invalid 'hours' field entry. ID: " + id_str)
            return

        retain_start = datetime.datetime.now() + datetime.timedelta(hours=-account.hours)        
        
        try:
            auth = tweepy.OAuthHandler(settings.CONSUMER_KEY, settings.CONSUMER_SECRET)
            auth.set_access_token(account.oauth_key, account.oauth_secret)
            api = tweepy.API(auth)
            deleted = 0
            for status in tweepy.Cursor(api.user_timeline, since_id=account.activated_status_id).items():
                if status.created_at < account.activated_on:
                    break
                if status.created_at < retain_start:
                    logging.warning("> Removing the time when " + account.display_name() + " said: " + status.text)
                    api.destroy_status(status.id)
                    deleted += 1
                if deleted >= 25:
                    break
        except Exception:
            logging.error('Some problem connecting to Twitter')
        
class FAQPage(webapp.RequestHandler):
    def get(self):
        current_user = users.get_current_user()
        if current_user:
            url = users.create_logout_url(self.request.uri)
        else:
            url = users.create_login_url(self.request.uri)

        template_values = {
            'current_user': current_user,
            'url': url,
            }

        path = os.path.join(os.path.dirname(__file__), 'templates/faq.html')
        self.response.out.write(template.render(path, template_values))
    
def main():
    application = webapp.WSGIApplication(
                                     [('/', MainPage),
                                      (PATH_HOME, HomePage),
                                      ('/user/edit', EditPage),
                                      (r'/user/update/(.*)', Update),
                                      ('/user/add', AddPage),                                      
                                      (PATH_CALLBACK, CallbackPage),
                                      ('/admin/delegator', CleaningDelegator),
                                      ('/admin/worker', CleaningWorker),
                                      ('/public/faq', FAQPage)],
                                     debug=True)
    util.run_wsgi_app(application)


if __name__ == '__main__':
    main()
