from flask import render_template

from app.projects import projects_bp


@projects_bp.route('/projects')
def index():
    return render_template('projects.html', active_page='projects')
