import os
from dotenv import load_dotenv
from website import create_app

load_dotenv()  # reads .env into os.environ before the app initializes

app = create_app()

if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug)
