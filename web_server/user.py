from flask.globals import current_app
from flask_login import UserMixin

import sys, traceback  # debugging

from db import get_db

class User(UserMixin):
    """
    The methods of this class are determined by the demands of the 
    `login_manager` library. For example, the fact that `get` returns
    a `None` if it fails to find the user.
    """

    def __init__(self, id, name, email, profile_pic, status="enabled", clockwork_api_key=None):
        """
        This constructor is called only by the `get` method.
        We never call it directly.
        """
        self.id = id
        self.name = name
        self.email = email
        self.profile_pic = profile_pic
        self.status = status
        self.clockwork_api_key = clockwork_api_key

    @staticmethod
    def get(id:str):
        """
        Returns a tuple (user:User or None, error_msg:str).
        """

        mc = get_db()[current_app.config["MONGODB_DATABASE_NAME"]]

        L = list(mc['users'].find({'id': id}))
        # This is not an error from which we expect to be able to recover gracefully.
        # It could happen if you copied data from your database directly
        # using an external script, and ended up with many instances of your users.
        # In that case, you might have other issues as well, so let's not even try
        # to just return the first instance of that user (ignoring the rest),
        # because that might hide more problems downstream.
        if len(L) not in [0, 1]:
            print("Found %d users with id %s. This can't happen." % (len(L), id))
            return None
        # this is fine, and the user will just get created by the parent code
        elif len(L) == 0:
            return None  #, f"Found no user in the database for id {id}."
        else:
            e = L[0]
            user = User(
                id=id,
                name=e['name'],
                email=e['email'],
                profile_pic=e['profile_pic'],
                status=e['status'],
                clockwork_api_key=e['clockwork_api_key'])
            print("Retrieved entry for user with email %s." % e['email'])

            # traceback.print_stack(file=sys.stdout)

            # Note that, at this point, it might be the case that the returned
            # user has status "disabled". The parent code will have to refrain
            # from continuing further if that's the case.
            return user

    @staticmethod
    def add_to_database(id, name, email, profile_pic, status="enabled", clockwork_api_key=None):
        """
        Create the entry in the database.
        Note that this method does not return the actual instance of User,
        but it just returns None.

        Returns a tuple (success:bool, error_msg:str).
        """

        if status not in ['enabled', 'disabled']:
            # Note that testing that the status is contained in the list enum values
            # is NOT the same as testing that the user's status is equal to "enabled".
            # Those are two different things entirely.
            return False, f"Invalid status {status}."

        user_is_valid, error_msg = User.validate_before_creation(id, name, email)
        if not user_is_valid:
            return False, f"Failed to validate the user. {error_msg}"

        if clockwork_api_key is None or len(clockwork_api_key) == 0:
            clockwork_api_key = get_new_clockwork_api_key()

        mc = get_db()[current_app.config["MONGODB_DATABASE_NAME"]]
        e = {'id': id,
            'name': name,
            'email': email,
            'profile_pic': profile_pic,
            'status': status,
            'clockwork_api_key': clockwork_api_key
        }
        mc['users'].update_one({'id': id}, {"$set": e}, upsert=True)
        # No need to do a "commit" operation or something like that.
        print("Created entry for user with email %s." % e['email'])
        return True, ""


    @staticmethod
    def validate_before_creation(id, name, email):
        """
        This is where you can add conditions to restrict the users being created.
        Otherwise, the system will create any user that gets authenticated by Google.

        Returns a tuple (success:bool, error_msg:str).
        """
        # There are already mechanisms in place to that login
        # is restricted to users within the organization only,
        # but let's add this check on top of it.
        if not email.endswith('@mila.quebec'):
            return False, "We accept only accounts @mila.quebec ."

        return True, ""


def get_new_clockwork_api_key(nbr_of_hex_characters = 32):
    """
    Creates a string with 32 hex characters one after the other.
    """
    import numpy as np
    return "".join(
            '%0.2x' % np.random.randint(low=0, high=256)
            for _ in range(nbr_of_hex_characters // 2))
