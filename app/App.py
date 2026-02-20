from flask import Flask, render_template, request, redirect
from GoogleCal import *
import GoogleCal
from yahoo import yfintut
import yahoo
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

#set FLASK_APP=App.py

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///blog.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

@app.route('/name')
def test(name):
	# return '<h1> Hello World {}!</h1>'.format(name)
	return '<h1>	Hello Nelson</h1>'


@app.route("/blog")
def blog():
    posts = BlogPost.query.order_by(BlogPost.created.desc()).all()
    return render_template("blog.html", posts=posts)


@app.route("/blog/<int:post_id>")
def blog_details(post_id):
    post = BlogPost.query.get_or_404(post_id)
    return render_template("blog_details.html", post=post)


@app.route("/create-post", methods=["GET", "POST"])
def create_post():
    if request.method == "POST":
        title = request.form.get("title")
        content = request.form.get("content")

        new_post = BlogPost(title=title, content=content)
        db.session.add(new_post)
        db.session.commit()

        return redirect("/blog")

    return render_template("create_post.html")


@app.route('/')
def index():
	return render_template('index.html')

@app.route('/courses')
def course():
	return render_template('courses.html')

@app.route('/personal')
def personal():
	return render_template('indexp.html')

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

		Run = True
		Data = yahoo.yfintut(Company)
		for i in Data:
			print(i)

		return render_template('yahoof.html', run = Run, CompanyName=Data[0], Open=Data[1], High=Data[2],Close=Data[3], Low=Data[4], Date=Data[5])

	return render_template('yahoof.html')

class BlogPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created = db.Column(db.DateTime, default=datetime.utcnow)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)