from flask import Flask, render_template, request, redirect
from GoogleCal import *
import GoogleCal
from yahoo import yfintut
import yahoo

#set FLASK_APP=App.py

app = Flask(__name__)

@app.route('/name')
def test(name):
	# return '<h1> Hello World {}!</h1>'.format(name)
	return '<h1>	Hello Nelson</h1>'

@app.route('/')
def index():
	return render_template('index.html')

@app.route('/courses')
def course():
	return render_template('courses.html')

@app.route('/googlecalender', methods=["GET", "POST"])
def GoogleCalender():
	if request.method == "POST":
		req = request.form
		print(req)

		title = req.get("title")
		location = req.get("location")
		decription = req.get("decription")
		stime = req.get("stime")
		etime = req.get("etime")
		sdate = req.get("sdate")
		edate = req.get("edate")
		email = req.get("email")

		calender.CreateEvent(title,stime,etime,sdate,edate,email, location, decription)
		return redirect(request.url)
	return render_template('GoogleCalander.html')

# @app.route('/yahoof')
# def yahoof():

# 	return render_template('yahoof.html', myfunc=yfintut )

@app.route('/yahoof', methods=["GET", "POST"])
def yahoof():
	if request.method == "POST":
		req = request.form
		print(req)
		print("--------------------------")
		Company = req.get("Company")
		print("--------------------------")
		
		# temp.append(investment)
		# temp.append(priceOpen)
		# temp.append(priceHigh)
		# temp.append(priceLow)
		# temp.append(pricelast)
		#+temp.append(today)

		Run = True
		Data = yahoo.yfintut(Company)
		for i in Data:
			print(i)

		return render_template('yahoof.html', run = Run, CompanyName=Data[0], Open=Data[1], High=Data[2],Close=Data[3], Low=Data[4], Date=Data[5])

	return render_template('yahoof.html')