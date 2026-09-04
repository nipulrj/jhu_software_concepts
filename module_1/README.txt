Module 1 - Personal Portfolio Website
======================================

Requirements
------------
- Python 3.10 or higher

Setup
-----
Install the dependencies:

   pip install -r requirements.txt

Running the site
-----------------
From the module_1 folder, run:

   python run.py

The site will start at:

   http://localhost:8080

Pages
-----
- Home:     http://localhost:8080/
- Contact:  http://localhost:8080/contact
- Projects: http://localhost:8080/projects

Project structure
------------------
run.py                 - application entry point
app/__init__.py        - Flask application factory, registers blueprints
app/home/               - Home page blueprint
app/contact/             - Contact page blueprint
app/projects/            - Projects page blueprint
app/templates/           - Jinja2 HTML templates (shared base.html + one per page)
app/static/              - CSS and image assets
