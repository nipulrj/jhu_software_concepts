from flask import Flask


def create_app():
    app = Flask(__name__)

    from app.home import home_bp
    from app.contact import contact_bp
    from app.projects import projects_bp

    app.register_blueprint(home_bp)
    app.register_blueprint(contact_bp)
    app.register_blueprint(projects_bp)

    return app
