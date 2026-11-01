from sqlalchemy import text
from storage.db import engine

with engine.connect() as conn:
    user = conn.execute(
        text('SELECT id, name, email, role FROM users WHERE email = :email'),
        {'email': 'sarthwaghela7@gmail.com'}
    ).fetchone()
    
    if user:
        user_id, name, email, role = user
        print(f'User: {name}')
        print(f'Email: {email}')
        print(f'Role in DB: "{role}"')
        print(f'Role type: {type(role)}')
        is_super = role in ('superuser', 'super_user')
        print(f'Is superuser check: {is_super}')
    else:
        print('User not found')
