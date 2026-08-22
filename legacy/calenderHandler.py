from GoogleCal import *
import cgi

form = cgi.FieldStorage()
title =  form.getvalue('title')
location =  form.getvalue('location')
decription =  form.getvalue('decription')
stime =  form.getvalue('stime')
etime =  form.getvalue('etime')
email =  form.getvalue('email')

CreateEvent(title,stime,etime,StartDate,EndDate,Emails, Location, details)

