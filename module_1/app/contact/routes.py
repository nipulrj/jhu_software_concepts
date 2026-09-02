from flask import render_template

from app.contact import contact_bp


@contact_bp.route('/contact')
def index():
    return render_template('contact.html', active_page='contact')
