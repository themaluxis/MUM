"""Add user_media_access_id to MediaStreamHistory

Revision ID: add_user_media_access_to_stream_history
Revises: 23fd7378a0fe
Create Date: 2025-01-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_user_media_access_stream'
down_revision = 'fix_user_app_access_nullable'
branch_labels = None
depends_on = None


def upgrade():
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('media_stream_history', schema=None) as batch_op:
        # Check if column already exists before adding
        try:
            # Add the new user_media_access_id column
            batch_op.add_column(sa.Column('user_media_access_id', sa.Integer(), nullable=True))
        except Exception as e:
            # Column might already exist, check if it's a duplicate column error
            if 'duplicate column name' not in str(e).lower():
                raise e
        
        # Make user_app_access_id nullable (it was previously NOT NULL)
        batch_op.alter_column('user_app_access_id', nullable=True)
        
        # Create foreign key constraint for user_media_access_id
        try:
            batch_op.create_foreign_key(
                'fk_media_stream_history_user_media_access_id',
                'user_media_access',
                ['user_media_access_id'], 
                ['id']
            )
        except Exception as e:
            # Foreign key might already exist
            if 'already exists' not in str(e).lower() and 'duplicate' not in str(e).lower():
                raise e
        
        # Create index for performance
        try:
            batch_op.create_index('ix_media_stream_history_user_media_access_id', ['user_media_access_id'])
        except Exception as e:
            # Index might already exist
            if 'already exists' not in str(e).lower() and 'duplicate' not in str(e).lower():
                raise e


def downgrade():
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('media_stream_history', schema=None) as batch_op:
        # Remove the index
        try:
            batch_op.drop_index('ix_media_stream_history_user_media_access_id')
        except Exception:
            pass  # Index might not exist
        
        # Remove the foreign key constraint
        try:
            batch_op.drop_constraint('fk_media_stream_history_user_media_access_id', type_='foreignkey')
        except Exception:
            pass  # Constraint might not exist
        
        # Remove the column
        try:
            batch_op.drop_column('user_media_access_id')
        except Exception:
            pass  # Column might not exist
        
        # Make user_app_access_id NOT NULL again (this might fail if there are NULL values)
        try:
            batch_op.alter_column('user_app_access_id', nullable=False)
        except Exception:
            pass  # This might fail if there are NULL values, which is expected