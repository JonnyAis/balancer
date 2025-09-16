# %% install
#!pip install rauth
#!pip install webbrowser
#!pip install pandas
#!pip install sleep
#!pip install numpy
#!pip install pytz
#!pip install scipy
#!pip install pandas-market-calendars
#%% import
from rauth import OAuth1Service
import webbrowser
import pandas as pd
import numpy as np
import time
import json
import itertools
import random
import string
import itertools
import math
from scipy.optimize import minimize
from pytz import timezone
import pandas_market_calendars as mcal
from datetime import datetime, timedelta
import timeit
from config_file import etrade_key, etrade_secret 

# Use consumer_key and consumer_secret in your script
print(etrade_key)
print(etrade_secret)

# %% creates calendar
nyse = mcal.get_calendar('NYSE')

schedule = nyse.schedule(start_date=datetime.strftime(datetime.now(),"%Y-%m-%d"), end_date=datetime.strftime(datetime.now()+ timedelta(days = 30),"%Y-%m-%d"))

# %%
SANDBOX = False
# %%
def getSession():
    # Create a session
    
    if SANDBOX:
        base_url = 'https://apisb.etrade.com'
    else:
        base_url = 'https://api.etrade.com'
        
    service = OAuth1Service(
              name = 'etrade',
              consumer_key = etrade_key,
              consumer_secret = etrade_secret,
              request_token_url = 'https://api.etrade.com/oauth/request_token', 
              access_token_url = 'https://api.etrade.com/oauth/access_token',
              authorize_url = 'https://us.etrade.com/e/t/etws/authorize?key={}&token={}',
              base_url = base_url)

    # Get request token and secret    
    oauth_token, oauth_token_secret = service.get_request_token(params = 
                                  {'oauth_callback': 'oob', 
                                   'format': 'json'})

    auth_url = service.authorize_url.format(service.consumer_key, oauth_token)
    
    # Get verifier (direct input in console, still working on callback)
    webbrowser.open(auth_url)
    time.sleep(1)
    verifier = input('Please input the verifier: ')

    return service.get_auth_session(oauth_token, oauth_token_secret, params = {'oauth_verifier': verifier})
# %% Create a session
session = getSession()