from flask import render_template

from app.home import home_bp


@home_bp.route('/')
def index():
    return render_template('home.html', active_page='home')
