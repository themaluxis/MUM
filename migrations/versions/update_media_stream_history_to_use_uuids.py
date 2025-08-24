"""Update MediaStreamHistory to use UUIDs for foreign keys

Revision ID: update_media_stream_history_uuids
Revises: add_global_id_uuids
Create Date: 2024-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'update_media_stream_history_uuids'
down_revision = 'rename_server_name_fields'
branch_labels = None
depends_on = None

def upgrade():
    # Check if columns already exist before attempting to add them
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = [col['name'] for col in inspector.get_columns('media_stream_history')]
    
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('media_stream_history', schema=None) as batch_op:
        # Add new UUID-based foreign key columns (only if they don't exist)
        if 'user_app_access_uuid' not in columns:
            batch_op.add_column(sa.Column('user_app_access_uuid', sa.String(36), nullable=True))
        
        if 'user_media_access_uuid' not in columns:
            batch_op.add_column(sa.Column('user_media_access_uuid', sa.String(36), nullable=True))
        
        # Create indexes for the new UUID columns (check if they already exist)
        indexes = [idx['name'] for idx in inspector.get_indexes('media_stream_history')]
        
        if 'ix_media_stream_history_user_app_access_uuid' not in indexes:
            batch_op.create_index('ix_media_stream_history_user_app_access_uuid', ['user_app_access_uuid'])
        
        if 'ix_media_stream_history_user_media_access_uuid' not in indexes:
            batch_op.create_index('ix_media_stream_history_user_media_access_uuid', ['user_media_access_uuid'])
    
    # Migrate existing data: populate UUID columns based on existing integer foreign keys
    # For user_app_access_id -> user_app_access_uuid
    op.execute("""
        UPDATE media_stream_history 
        SET user_app_access_uuid = (
            SELECT uuid FROM user_app_access 
            WHERE user_app_access.id = media_stream_history.user_app_access_id
        )
        WHERE user_app_access_id IS NOT NULL
    """)
    
    # For user_media_access_id -> user_media_access_uuid
    op.execute("""
        UPDATE media_stream_history 
        SET user_media_access_uuid = (
            SELECT uuid FROM user_media_access 
            WHERE user_media_access.id = media_stream_history.user_media_access_id
        )
        WHERE user_media_access_id IS NOT NULL
    """)
    
    # Check current state before making changes
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    current_columns = [col['name'] for col in inspector.get_columns('media_stream_history')]
    current_indexes = [idx['name'] for idx in inspector.get_indexes('media_stream_history')]
    current_fks = [fk['name'] for fk in inspector.get_foreign_keys('media_stream_history')]
    
    # Use batch mode for constraint and column operations
    with op.batch_alter_table('media_stream_history', schema=None) as batch_op:
        # Drop old foreign key constraints (if they exist)
        if 'fk_media_stream_history_user_app_access_id' in current_fks:
            batch_op.drop_constraint('fk_media_stream_history_user_app_access_id', type_='foreignkey')
        
        if 'fk_media_stream_history_user_media_access_id' in current_fks:
            batch_op.drop_constraint('fk_media_stream_history_user_media_access_id', type_='foreignkey')
        
        # Drop old indexes (if they exist)
        if 'ix_media_stream_history_user_app_access_id' in current_indexes:
            batch_op.drop_index('ix_media_stream_history_user_app_access_id')
        
        if 'ix_media_stream_history_user_media_access_id' in current_indexes:
            batch_op.drop_index('ix_media_stream_history_user_media_access_id')
        
        # Drop old integer foreign key columns (if they exist)
        if 'user_app_access_id' in current_columns:
            batch_op.drop_column('user_app_access_id')
        
        if 'user_media_access_id' in current_columns:
            batch_op.drop_column('user_media_access_id')
        
        # Add new foreign key constraints to UUID columns (if they don't exist)
        if 'fk_media_stream_history_user_app_access_uuid' not in current_fks:
            batch_op.create_foreign_key('fk_media_stream_history_user_app_access_uuid', 'user_app_access', ['user_app_access_uuid'], ['uuid'])
        
        if 'fk_media_stream_history_user_media_access_uuid' not in current_fks:
            batch_op.create_foreign_key('fk_media_stream_history_user_media_access_uuid', 'user_media_access', ['user_media_access_uuid'], ['uuid'])

def downgrade():
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('media_stream_history', schema=None) as batch_op:
        # Add back old integer foreign key columns
        batch_op.add_column(sa.Column('user_app_access_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('user_media_access_id', sa.Integer(), nullable=True))
        
        # Create indexes for the old integer columns
        batch_op.create_index('ix_media_stream_history_user_app_access_id', ['user_app_access_id'])
        batch_op.create_index('ix_media_stream_history_user_media_access_id', ['user_media_access_id'])
    
    # Migrate data back: populate integer columns based on UUID foreign keys
    # For user_app_access_uuid -> user_app_access_id
    op.execute("""
        UPDATE media_stream_history 
        SET user_app_access_id = (
            SELECT id FROM user_app_access 
            WHERE user_app_access.uuid = media_stream_history.user_app_access_uuid
        )
        WHERE user_app_access_uuid IS NOT NULL
    """)
    
    # For user_media_access_uuid -> user_media_access_id
    op.execute("""
        UPDATE media_stream_history 
        SET user_media_access_id = (
            SELECT id FROM user_media_access 
            WHERE user_media_access.uuid = media_stream_history.user_media_access_uuid
        )
        WHERE user_media_access_uuid IS NOT NULL
    """)
    
    # Use batch mode for constraint and column operations
    with op.batch_alter_table('media_stream_history', schema=None) as batch_op:
        # Drop UUID foreign key constraints
        try:
            batch_op.drop_constraint('fk_media_stream_history_user_app_access_uuid', type_='foreignkey')
        except Exception:
            pass  # Constraint might not exist
        
        try:
            batch_op.drop_constraint('fk_media_stream_history_user_media_access_uuid', type_='foreignkey')
        except Exception:
            pass  # Constraint might not exist
        
        # Drop UUID indexes
        try:
            batch_op.drop_index('ix_media_stream_history_user_app_access_uuid')
        except Exception:
            pass  # Index might not exist
        
        try:
            batch_op.drop_index('ix_media_stream_history_user_media_access_uuid')
        except Exception:
            pass  # Index might not exist
        
        # Drop UUID columns
        try:
            batch_op.drop_column('user_app_access_uuid')
        except Exception:
            pass  # Column might not exist
        
        try:
            batch_op.drop_column('user_media_access_uuid')
        except Exception:
            pass  # Column might not exist
        
        # Add back old foreign key constraints
        try:
            batch_op.create_foreign_key('fk_media_stream_history_user_app_access_id', 'user_app_access', ['user_app_access_id'], ['id'])
        except Exception as e:
            if 'already exists' not in str(e).lower() and 'duplicate' not in str(e).lower():
                raise e
        
        try:
            batch_op.create_foreign_key('fk_media_stream_history_user_media_access_id', 'user_media_access', ['user_media_access_id'], ['id'])
        except Exception as e:
            if 'already exists' not in str(e).lower() and 'duplicate' not in str(e).lower():
                raise e