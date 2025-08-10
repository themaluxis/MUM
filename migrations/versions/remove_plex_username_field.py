"""Remove plex_username field from User model

Revision ID: remove_plex_username
Revises: [previous_revision]
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'remove_plex_username'
down_revision = 'add_service_join_date'  # This should be set to the latest migration
branch_labels = None
depends_on = None


def upgrade():
    """
    Migrate any remaining data from plex_username to primary_username and remove the column
    """
    # First, migrate any remaining data where primary_username is NULL but plex_username has data
    connection = op.get_bind()
    
    # Update users where primary_username is NULL but plex_username has data
    connection.execute(sa.text("""
        UPDATE users 
        SET primary_username = plex_username 
        WHERE primary_username IS NULL 
        AND plex_username IS NOT NULL 
        AND plex_username != ''
    """))
    
    # For any users that still have NULL primary_username, set a default value
    connection.execute(sa.text("""
        UPDATE users 
        SET primary_username = COALESCE(plex_email, CONCAT('user_', id))
        WHERE primary_username IS NULL
    """))
    
    # Now remove the plex_username column
    with op.batch_alter_table('users', schema=None) as batch_op:
        # Drop the index first
        batch_op.drop_index('ix_users_plex_username')
        # Drop the column
        batch_op.drop_column('plex_username')


def downgrade():
    """
    Re-add the plex_username column (data will be lost)
    """
    with op.batch_alter_table('users', schema=None) as batch_op:
        # Add the column back
        batch_op.add_column(sa.Column('plex_username', sa.String(length=255), nullable=True))
        # Recreate the index
        batch_op.create_index('ix_users_plex_username', ['plex_username'], unique=True)
    
    # Optionally copy data back from primary_username to plex_username
    # (This is a lossy operation since we can't distinguish original sources)
    connection = op.get_bind()
    connection.execute(sa.text("""
        UPDATE users 
        SET plex_username = primary_username 
        WHERE primary_username IS NOT NULL
    """))