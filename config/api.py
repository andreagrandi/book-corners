from ninja import NinjaAPI

from users.api import auth_router

api = NinjaAPI(title="Little Free Libraries API")
api.add_router("/auth/", auth_router)
