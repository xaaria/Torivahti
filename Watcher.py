import urllib.request
import urllib.parse
import http.client
from bs4 import BeautifulSoup
import datetime as dt
import re
import pytz
import logging as log
import boto3
import pub_date_parser


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
    self.recipients = recipients
    
    # SES:
    if len(recipients) == 0:
      print("0 recipients!")



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
      cloudwatch    = aws_session.client('events') # was boto3?
      
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

  BASE_URL                    = "https://www.tori.fi/"
  URL_PREFIX                  = "&cg=0&w=111&st=s&st=g&ca=18&l=0&md=th"
  TZ                          = pytz.timezone("Europe/Helsinki")

  # How many seconds is the mismatch between servers. Used when comparing timestamps. This val is ADDED to tori.fi's timestamp.
  # This val. can be overwritten
  server_time_offset_secs     = 0 



  def __init__(self, name="Unnamed Watcher", area=None, keywords=[], timespan_sec=600, price_limit=(0, 100,000)):
    
    if len(keywords) == 0 or keywords == None:
      print("Zero keywords (empty list) or None given!")

    if timespan_sec > 2*24*3600:
      timespan_sec = 2*86400 # 2 days
      print("Products from day before yesterday are ignored (in this version)! Timespan set to 2 days.")

    # ---

    self.name             = name
    self.area             = area        # For example 'pirkanmaa'
    self.keywords         = list(map(lambda kw: kw.strip(), keywords) ) # remove whitspace around the kws 
    self.timespan_sec     = timespan_sec
    self.price_limit_min  = price_limit[0]
    self.price_limit_max  = price_limit[1]
    self.last_run         = None

    
    
    
  def run(self):
    
    """ 
      Runs the Watcher script.
      Returns list of products that meet the criterions
    """

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

      # el is the main element of the product
      for el in bs.find_all("a", {"class": "item_row"}):
        
        desc_el     = el.find("div", {"class": "desc"})
        
        try:
          link = el["href"]
        except Exception:
          link = "Link Exception"
        
        try:
          name        = desc_el.find("div", {"class": "li-title"}).text
        except Exception:
          name = "Name Exception"
        
        try:
          price_str       = desc_el.find("div", {"class": "list-details-container"}).find("p", {"class": "list_price"}).text
          price           = int(re.search("\d{1,}", price_str)[0])
        except Exception:
          price = None

        try: 
          pub_time_str      = desc_el.find("div", {"class": "date-cat-container"}).find("div", {"class": "date_image"}).text
          
          # Get the actual pub time datetime object from the string
          pub_time          = pub_date_parser.get_timestamp(pub_time_str)
  
        except Exception as e:
          print(e)
          pub_time = None
        
        print( str.format("'{}', {} €, {}", name, price, pub_time) )

        # Is this product viable? 
        if self.is_within_timespan(pub_time) and self.is_within_pricelimit(price):
          p = Product( name, price, link, pub_time )
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

    # Replace spaces with '%20' and add "+OR+" between keywords
    keyword_part = "+OR+".join( list(map(lambda s: s.replace(" ", "%20"), self.keywords ) ) )
    keyword_part = urllib.parse.quote_plus(keyword_part)
    return str.format("{}{}?q={}{}", self.BASE_URL, self.area, keyword_part, self.URL_PREFIX)


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


  def is_within_timespan(self, ts):
    
    if ts == None: 
      return False
    
    try:
    
      comp = (dt.datetime.now(self.TZ) - dt.timedelta(seconds=self.timespan_sec + self.server_time_offset_secs))
      print( str.format("Comparing: {} >= {} | Offset is: {} s", ts, comp, self.timespan_sec + self.server_time_offset_secs) )
      return ts >= comp
    except Exception as e:
      print(e)
      return False
    else:
      return False




  def __str__(self):
    return str.format(
      "Watcher: {}.\n\tSearch keywords: {}\n\tarea: '{}'\n\tTimespan{} s. (offset {} s.)\n\tPrice between {}-{} €.\n\tURL: {}\n", 
      self.name, ", ".join(self.keywords), self.area, self.timespan_sec, self.server_time_offset_secs, self.price_limit_min, self.price_limit_max, self.generate_search_url()
    )

# end of class


class Product:

  """ Product class """
  def __init__(self, name, price, link, pub_time):
    self.name       = name
    self.price      = price
    self.link       = link
    self.pub_time   = pub_time

  def __str__(self):
    
    time = ""
    if isinstance(self.pub_time, dt.datetime) == True:
      time = self.pub_time.strftime("%d.%m. %H:%M")

    return str.format("<b><a href={}>{}</a></b><a><br/>[{} €, {}]", self.link, self.name, self.price, time )