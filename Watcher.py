import urllib.request
import urllib.parse
import http.client
from bs4 import BeautifulSoup
import datetime as dt
import re
import pytz
import logging as log
import boto3

class AWSHandler:

  """
    Takes in Watcher object and does the magic.

    Params
    - watcher: Watcher object
    - profile_name: AWS [profile_name]
    - event: AWS event object
    - context: AWS context object
    - recipients: list of emails
  """

  # This address must be verified with Amazon SES.
  SENDER        = "Hakuvahti <okkomarble@gmail.com>"
  MAIL_CHARSET  = "UTF-8"

  def __init__(self, watcher, profile_name, event, context, recipients=[]):
    self.watcher        = watcher
    self.profile_name   = profile_name
    self.event          = event
    self.context        = context
    self.recipients     = recipients
    
    # SES:
    if len(recipients) == 0:
      print("[Warning] 0 recipients set!")



  def run(self):

    """
      Runs the main script.
      Returns the number of included products or -1 if error.
    """

    prods = []

    # Create boto3 entities. Either running on AWS or local
    try:
      aws_session     = boto3.Session(profile_name=self.profile_name) # Settings located in ~/.aws/
    except Exception as identifier:
      aws_session     = boto3.Session()
      print(identifier)

    print("Boto3 session OK")

    # Try to create SES client
    try:
      ses           = aws_session.client('ses')
    except Exception as e:
      print(e)
      return -1

    # Try to create CloudWatch client and retrieve the schedule. If fails, use the Watcher-obj. timespan variable.
    try:
      
      # Get the CloudWatch object that triggered this lambda function.
      # Please see: https://docs.aws.amazon.com/lambda/latest/dg/with-scheduled-events.html
      cloudwatch    = aws_session.client('events')
      
      # Event dict. holds the details of the event that launced this sript. Get the CloudWatch timer schedule.
      event_arn   = self.event.get("resources")[0]  # "arn:aws:events:us-east-1:xxx:rule/hakuvahti"
      rule_name   = re.findall("\/(.*)$", event_arn)[0]
      rule        = cloudwatch.describe_rule(Name=rule_name)
      minutes     = int( re.findall("\d{1,2}", rule.get("ScheduleExpression"))[0] )

      print( str.format("[OK] rule_name: '{}', mins: {}", rule_name, minutes) )
      
      # Overwrite the original timespan if we got the correct timespan
      if minutes > 0:
        self.watcher.timespan_sec = (60*minutes) #+ 1000 # +offset is added later!

    except Exception as e:
      print(str.format("Getting correct schedule failed: {}", e) )
      # Continue as normal. Will use Watchers timespan_sec's value. 


    try:
      prods   = self.watcher.run()
    except Exception as e:
      print(e)
      return -1
    

    # If retrieved 1 or more products, try to send an email
    if len(prods) >= 1:

      # Make the email content and set other details
      content   = str.format(
        "{} new product(s)!<br/><br/>{}<br/><hr/>Details:\n{}", 
        len(prods),
        self.watcher.get_product_list(prods),
        self.watcher
      )

      subject   = str.format("({}) Hakuvahti '{}'", len(prods), self.watcher.name)
      
      destination = { 
        "ToAddresses": self.recipients
      }

      message = {
        'Subject': {
          'Data': subject,
          'Charset':  self.MAIL_CHARSET
        },
        'Body': {
          'Html': {
            'Data': content,
            'Charset':  self.MAIL_CHARSET
          },
          'Text': {
            'Data': content,
            'Charset': self.MAIL_CHARSET
          }
        }
      }


      try:
        response = ses.send_email(Destination=destination, Source=self.SENDER, Message=message)
      except Exception as mail_ex:
        print(mail_ex)
      else:
        msg_id = response['MessageId']
        print( str.format("Email(s) sent! Message ID: {}", msg_id) )
        return len(prods)

    else:
      print("0 Products found...")
      return 0


  def __str__(self):
    return str.format("AWS Handler\n\tWatcher obj. name: {}\n\tRecipient(s): {}", self.watcher.name, self.recipients)



class Watcher:

  BASE_URL                    = "https://www.tori.fi"   # no leading /
  
  # param w contains the area ID
  URL_PREFIX                  = "&cg=0&w=_AREA_CODE_&st=s&st=g&ca=18&l=0&md=th" # _AREA_CODE will be replaced with self.area_code
  AREA_CODE_REPLACE           = "_AREA_CODE_" # replace me with the correct area_code
  
  TZ                          = pytz.timezone("Europe/Helsinki")

  # How many seconds is the mismatch between servers. Used when comparing timestamps. This val is ADDED to tori.fi's timestamp.
  # This val. can be overwritten
  server_time_offset_secs     = 0 

  # Dynamo DB
  DYNAMO_TABLE_NAME         = "lautisvahti"
  DYNAMO_OBJ_PRIMARY_KEY    = 1234    # The default objects primary key where we store product values of the latest run


  def __init__(self, name="Watcher", area_code=3, keywords=[], timespan_sec=600, price_limit=(0, 100,000)):
    
    if len(keywords) == 0 or keywords == None:
      print("Zero keywords (empty list) or None given!")

    if timespan_sec > 2*24*3600:
      timespan_sec = 2*86400 # 2 days
      print("Products from day before yesterday are ignored (in this version)! Timespan set to 2 days.")

    # ---

    self.name             = name
    self.area_code        = area_code # Area ID. default  3 (entire Finland). 111 pirkanmaa
    self.keywords         = list(map(lambda kw: kw.strip(), keywords) ) # remove whitspace around the kws 
    self.timespan_sec     = timespan_sec
    self.price_limit_min  = price_limit[0]
    self.price_limit_max  = price_limit[1]
    self.last_run         = None

    # Init Dynamo 
    self.dynamo           = boto3.client('dynamodb')
    
    
  def run(self):
    
    """ 
      Runs the Watcher script.
      Returns list of products that meet the criterions
    """
    print("Starting crawling...")

    if len(self.keywords) == 0:
      raise Exception("0 keywords! Can't run Watcher!")

    # Empty old products
    products        = []
    self.last_run   = dt.datetime.now()
    

    try:
      res = urllib.request.urlopen(self.generate_search_url())
    except Exception as http_ex:
      print(str.format("HTTP error: {}", http_ex) )
      return []

    if res.status == 200:

      html_data   = res.read().decode("latin-1")
      bs          = BeautifulSoup(html_data, 'html.parser')


      # Notice that different URL calls gives different HTML
      # el is the main element of the product
      # IF NO EXCEPTION IS THROWN, CHECK THAT HTML IS NOT CHANGED BY SITE OWNER
      for el in bs.find_all("a", {"class": "item_row_flex"}):
        
        id = el.get('id')
        #print("element id #", id)
        id = int(re.search("\d{1,}", id)[0])
        print("id as int:", id)
        #return []

        p = Product() # Create an empty product
        desc_el     = el.find("div", {"class": "desc_flex"})

        try:
          p.link = el["href"]
        except Exception:
          p.link = "Link Exception"
        
        try:
          p.name        = desc_el.find("div", {"class": "li-title"}).text
        except Exception:
          p.name = "Name Exception"
        
        try:
          price_str       = desc_el.find("div", {"class": "list-details-container"}).find("p", {"class": "list_price"}).text
          p.price         = int(re.search("\d{1,}", price_str)[0])
        except Exception:
          p.price = None

        try: 
          pub_time_str      = desc_el.find("div", {"class": "date-cat-container"}).find("div", {"class": "date_image"}).text
          
          # Get the actual pub time datetime object from the string
          pub_time          = pub_date_parser.get_timestamp(pub_time_str)
          if pub_time != None:
            p.pub_time = pub_time
          else:
            print("[Info] loop break")
            break # Exit this entire loop. If parsing fails, it's because prod. is too old and ts was not parsed.

  
        except Exception as e:
          print(e)
        
        print( str.format("'{}', {} â‚¬, {}", p.name, p.price, p.pub_time) )

        # Check if not already checked and if is within given price limits
        if not self.is_already_seen(id) and self.is_within_pricelimit(p.price):
          products.append(p)
          print( str.format("\t  Added product {} ", p) )
        else:
          print("\t  Criterion(s) were not fullfilled. Price too low/high or too old*.")

      print( 
        str.format("---\nScript ran successfully {} (retrieved {} products).\nURL: {}\n---\n", 
        dt.datetime.now(self.TZ).strftime("%d.%m. %H:%M.%S (%z)"), len(products), self.generate_search_url() ) 
      )


    else:
      print("HTTP request failed.")

    # finally
    return products


  def get_product_list(self, prods=[]):
    s = ""
    for pr in prods:
      s += str.format("{}<br/><br/>", pr)
    return s


  def print_products(self, prods=[], limit=None):
    for pr in prods[:limit]:
      print( pr )

  
  def generate_search_url(self):

    # Replace area code for 
    prefix = self.URL_PREFIX.replace( self.AREA_CODE_REPLACE , str(self.area_code), 1 )
    
    # Replace spaces with '%20' and add "+OR+" between keywords
    keyword_part = "+OR+".join( list(map(lambda s: s.replace(" ", "+"), self.keywords ) ) )
    keyword_part = urllib.parse.quote_plus(keyword_part)
    
    return str.format("{}/koko_suomi?q={}{}", self.BASE_URL, keyword_part, prefix)


  def is_within_pricelimit(self, price):

    """
      None is always valid. As price is not set (free?).
      We'll include it anyway.
    """
    if price == None:
      return True

    try: 
      return (price >= self.price_limit_min and price <= self.price_limit_max)
    except Exception as e:
      print(e)
      return False
    else:
      return False



  """
  Gets database from AWS Dynamo to see if this product is already seen. 
  Returns boolean. If False, an alert should be sent if meets other criteria

  Dynamo DB structure is 
  
    run_id: {
      NumberSet: { product ids }
    }

  Where run_id is a possible primary key for different runs 
  - But we use the same object (aka item)

  """
  def is_already_seen(self, product_id=-1):

    try:
      item = self.dynamo.get_item(TableName=self.DYNAMO_TABLE_NAME, Key={
        'run_id': { 'N': str(self.DYNAMO_OBJ_PRIMARY_KEY) } # Number type field (N)
      })

      item = item.get('Item')
      print( item.get('products') )

      # stored as number, but is as a string in python dict, lol
      if str(product_id) in item.get('products', {}).get('NS', {}):
        return True
      else:
        return False
    
    except Exception as e:
      raise(e)


  """
  Inserts product id to the item that keeps track of seen products
  """
  def insert_prodcut_dynamo(self, product_id):

    dynamo  = boto3.resource('dynamodb')
    table   = dynamo.Table(self.DYNAMO_TABLE_NAME)

    item = self.dynamo.get_item(TableName=self.DYNAMO_TABLE_NAME, Key={
      'run_id': { 'N': str(self.DYNAMO_OBJ_PRIMARY_KEY) } # Number type field (N)
    })

    # Append old value list
    print(item)
    prods = item.get('Item').get('products', {}).get('NS', []) # Comes as "NS": ["1234", "567"]

    # Append and change to set of numbers
    prods.append(product_id)
    prods = list(map(lambda el: int(el), prods) )
    set_ = set(prods)

    response = table.update_item(
      Key={
        'run_id': self.DYNAMO_TABLE_NAME
      },
      UpdateExpression="SET products=:prods",
      ExpressionAttributeValues={
        ':prods': set_
      },
      ReturnValues="UPDATED_NEW"
    )
    return response


# end of class


class Product:

  """ Product class """
  def __init__(self, id=None, name=None, price=None, link=None, pub_time=None):
    self.id         = id
    self.name       = name
    self.price      = price
    self.link       = link
    self.pub_time   = pub_time

  def __str__(self):
    
    time = "?"
    if isinstance(self.pub_time, dt.datetime) == True:
      time = self.pub_time.strftime("%d.%m. %H:%M")

    return str.format("<b><a href={}>{}</a></b><a><br/>[{}, {}]", self.link, self.name, self.price, time )