# an object of WSGI application
from flask import Flask, render_template

app = Flask(__name__)  # Flask constructor

# A decorator used to tell the application
# the URL is associated function
@app.route('/')
# Now our "hello world" is our home page
def home():
    return render_template('home.html')


@app.route('/about')
def about():
    return "The about page"


@app.route('/contact')
def contact():
    return "For more information Contact Liv and Joe!"


if __name__ == '__main__':
    # Run the application
    app.run(host='0.0.0.0', port=8080)
