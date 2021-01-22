import boto3
import logging as log
import Watcher
import re
import os

""" 

  Main file for tori.fi Watcher running in AWS Lambda.
  
  Requirements:
    - py 3 with libs: boto3, beautifulsoup4, pytz
    - AWS credentials (app keys if running local)
    - AWS SES verified email for sender
    - related code files / imports
    - CloudWatch timer that triggers this AWS lambda. Schedule must be X minutes (DO NOT USE CRON).

  AWS Lambda Environment variables include (only keywords is mandatory):
    'name': Name of the Watcher
    'area': tori.fi area identifier | defaults to 'pirkanmaa'
    'keywords': comma seperated list of keywords. Comma cannot be escaped. | for example "AAA, BBbbB, Cccc"
    'price_limit_min': Price min. Inclusive. None always included.
    'price_limit_max': --
    'timespan_sec': Default timespan the watcher will use. NOTE: This will be overwritten if CloudWatch rule's scedule is determined succesfully.
    'server_time_offset_secs': Offset extra seconds in case of delay and/or (tori.fi) server mismatch. ONLY define this if you know what you're doing!

  Set handler as 'main.lambda_handler', meaning this file and function named 'lambda_handler(event, context)'

  This script returns the number of found valid products or Exception.

  Author: Okko.P. 12/2019
"""


# The main function that lambda will call
def lambda_handler(event, context):

  watcher = Watcher.Watcher()
  aws_handler = Watcher.AWSHandler(watcher, None, event, context, [])

  # Configure Watcher through params
  try:
    watcher.name                      = os.environ['name']
    watcher.area_code                 = int(os.environ['area_code'])
    watcher.keywords                  = os.environ['keywords'].split(",") # array of keywords
    watcher.price_limit_min           = int(os.environ['price_limit_min']) 
    watcher.price_limit_max           = int(os.environ['price_limit_max'])
    watcher.timespan_sec              = int( os.environ['timespan_sec'] )

    # Confifure AWS Handler
    aws_handler.recipients            = os.environ['recipients'].split(",")
    
    # Other configuratations
    watcher.server_time_offset_secs   = int(os.environ['server_time_offset_secs'])
  
  except Exception as e:
    raise Exception( str.format("Exception while configurating (do you have all the fields correctly filled?): {}", e) )

  
  print( watcher )
  print( aws_handler )

  aws_handler = Watcher.AWSHandler(watcher, None, event, context, aws_handler.recipients)
  products = aws_handler.run()
  print( str.format("Returned: {}", products) )

  return products

    


# This script is first run when running locally!
if __name__ == '__main__':

  print("Running manually... calling lambda...")

  os.environ['name'] = "os.Lautapelit"
  os.environ['area_code'] = "3" 
  os.environ['keywords'] = "lautapelit,lautapeli,korttipeli,roolipeli,seurapeli,boardgame,lauta peli,pelisetti" #"lautapelit,korttipeli,seurapeli,lautapeli,boardgame,lauta peli"
  os.environ['price_limit_min'] = "1"
  os.environ['price_limit_max'] = "300"
  os.environ['timespan_sec'] = "100"
  os.environ['server_time_offset_secs'] = "0"
  os.environ['recipients'] = "okkomarble@gmail.com"

  lambda_handler(None, None)


