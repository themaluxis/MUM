"""Add email field to users table

Revision ID: d9aa2d719ac0
Revises: user_unification
Create Date: 2025-09-27 14:50:22.811853

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd9aa2d719ac0'
down_revision = 'user_unification'
branch_labels = None
depends_on = None


def upgrade():
    # Check if table exists before trying to operate on it
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    
    # Check if users table exists - if not, user unification hasn't completed
    if 'users' not in inspector.get_table_names():
        raise Exception("Users table doesn't exist. User unification migration hasn't completed yet.")
    
    # Simply add the email column to the existing users table
    with op.batch_alter_table('users', schema=None) as batch_op:
        # Check if email column already exists
        columns = [col['name'] for col in inspector.get_columns('users')]
        if 'email' not in columns:
            batch_op.add_column(sa.Column('email', sa.String(length=255), nullable=True))
        
        # Create email index if it doesn't exist
        indexes = [idx['name'] for idx in inspector.get_indexes('users')]
        if 'ix_users_email' not in indexes:
            batch_op.create_index('ix_users_email', ['email'], unique=False)


def downgrade():
    # Remove the email column and index
    with op.batch_alter_table('users', schema=None) as batch_op:
        conn = op.get_bind()
        inspector = sa.inspect(conn)
        
        # Drop email index if it exists
        indexes = [idx['name'] for idx in inspector.get_indexes('users')]
        if 'ix_users_email' in indexes:
            batch_op.drop_index('ix_users_email')
        
        # Drop email column if it exists
        columns = [col['name'] for col in inspector.get_columns('users')]
        if 'email' in columns:
            batch_op.drop_column('email')
