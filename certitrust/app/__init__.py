from flask import Flask
import os

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'dev-secret-key-change-later'
    app.config['DATABASE'] = os.path.join(app.instance_path, 'certitrust.db')

    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    from . import db
    db.init_app(app)

    from . import routes
    app.register_blueprint(routes.bp)

    from . import automation_routes
    app.register_blueprint(automation_routes.bp)

    # non-destructive upgrade of an existing database (adds missing columns)
    with app.app_context():
        if os.path.exists(app.config['DATABASE']):
            from .db import get_db
            from .services.automation import ensure_schema
            ensure_schema(get_db())

    return app

##grade_map = {
##        'FE)': 'O','fe)': 'O','FE': 'O','F)': 'O','[e)': 'O','[E)': 'O',
  ##      'E)': 'O','Oo': 'O','O)': 'O','0': 'O','OO': 'O','O0': 'O','0O': 'O',
    ##    'OE': 'O','|OO': 'O','OO|': 'O','oO': 'O','O': 'O','o': 'O','0': 'O',
##
  ##      'AT': 'A+','At': 'A+','A+': 'A+','A': 'A',
##
  ##      'B+': 'B+','Bt': 'B+','BT': 'B+','B': 'B',
##
  ####      'C': 'C',
      ##  'U': 'U',
    ##}