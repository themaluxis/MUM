"""Add unique constraint to server names

Revision ID: add_unique_server_names
Revises: complete_mum_migration
Create Date: 2025-01-16 20:20:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = 'add_unique_server_names'
down_revision = 'complete_mum_migration'
branch_labels = None
depends_on = None

def upgrade():
    # First, handle any duplicate server names by appending a number
    connection = op.get_bind()
    
    # Find duplicate names
    result = connection.execute(text("""
        SELECT name, COUNT(*) as count 
        FROM media_servers 
        GROUP BY name 
        HAVING COUNT(*) > 1
    """))
    
    duplicates = result.fetchall()
    
    # Rename duplicates by appending a number
    for duplicate in duplicates:
        name = duplicate[0]
        # Get all servers with this name, ordered by ID
        servers = connection.execute(text("""
            SELECT id FROM media_servers 
            WHERE name = :name 
            ORDER BY id
        """), {"name": name}).fetchall()
        
        # Keep the first one as-is, rename the rest
        for i, server in enumerate(servers[1:], start=2):
            new_name = f"{name} ({i})"
            connection.execute(text("""
                UPDATE media_servers 
                SET name = :new_name 
                WHERE id = :server_id
            """), {"new_name": new_name, "server_id": server[0]})
    
    # Now add the unique constraint
    with op.batch_alter_table('media_servers', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_media_servers_name', ['name'])

def downgrade():
    # Remove the unique constraint
    with op.batch_alter_table('media_servers', schema=None) as batch_op:
        batch_op.drop_constraint('uq_media_servers_name', type_='unique')