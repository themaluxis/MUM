"""Cleanup deprecated user columns from media_stream_history

Revision ID: cleanup_deprecated_user_columns
Revises: consolidate_media_stream_history_user_refs
Create Date: 2025-01-15 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'cleanup_deprecated_user_columns'
down_revision = 'consolidate_media_stream_history_user_refs'
branch_labels = None
depends_on = None


def upgrade():
    """
    Remove deprecated user_app_access_uuid and user_media_access_uuid columns
    """
    print("Starting cleanup of deprecated user columns...")
    
    # Check if columns still exist before trying to drop them
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('media_stream_history')]
    
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('media_stream_history', schema=None) as batch_op:
        # Drop old indexes first (if they exist)
        indexes = [idx['name'] for idx in inspector.get_indexes('media_stream_history')]
        
        if 'ix_media_stream_history_user_app_access_uuid' in indexes:
            try:
                batch_op.drop_index('ix_media_stream_history_user_app_access_uuid')
                print("Dropped ix_media_stream_history_user_app_access_uuid index")
            except Exception as e:
                print(f"Note: Could not drop ix_media_stream_history_user_app_access_uuid - {e}")
        
        if 'ix_media_stream_history_user_media_access_uuid' in indexes:
            try:
                batch_op.drop_index('ix_media_stream_history_user_media_access_uuid')
                print("Dropped ix_media_stream_history_user_media_access_uuid index")
            except Exception as e:
                print(f"Note: Could not drop ix_media_stream_history_user_media_access_uuid - {e}")
        
        # Drop old columns
        if 'user_app_access_uuid' in columns:
            batch_op.drop_column('user_app_access_uuid')
            print("Dropped user_app_access_uuid column")
        else:
            print("user_app_access_uuid column not found, skipping")
            
        if 'user_media_access_uuid' in columns:
            batch_op.drop_column('user_media_access_uuid')
            print("Dropped user_media_access_uuid column")
        else:
            print("user_media_access_uuid column not found, skipping")
    
    print("Cleanup of deprecated user columns completed successfully!")


def downgrade():
    """
    Re-add the deprecated columns (for rollback purposes)
    """
    print("Starting downgrade - re-adding deprecated user columns...")
    
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('media_stream_history', schema=None) as batch_op:
        # Re-add the old columns
        batch_op.add_column(sa.Column('user_app_access_uuid', sa.String(36), nullable=True))
        batch_op.add_column(sa.Column('user_media_access_uuid', sa.String(36), nullable=True))
        
        # Create indexes
        batch_op.create_index('ix_media_stream_history_user_app_access_uuid', ['user_app_access_uuid'])
        batch_op.create_index('ix_media_stream_history_user_media_access_uuid', ['user_media_access_uuid'])
    
    # Populate old columns based on user types
    print("Populating old columns based on user types...")
    
    connection = op.get_bind()
    
    # Restore user_app_access_uuid for LOCAL users
    print("Restoring user_app_access_uuid for LOCAL users...")
    from sqlalchemy.sql import text
    result1 = connection.execute(text("""
        UPDATE media_stream_history 
        SET user_app_access_uuid = user_uuid 
        WHERE user_uuid IN (
            SELECT uuid FROM users WHERE userType = 'LOCAL'
        )
    """))
    print(f"Restored {result1.rowcount} user_app_access_uuid records")
    
    # Restore user_media_access_uuid for SERVICE users
    print("Restoring user_media_access_uuid for SERVICE users...")
    result2 = connection.execute(text("""
        UPDATE media_stream_history 
        SET user_media_access_uuid = user_uuid 
        WHERE user_uuid IN (
            SELECT uuid FROM users WHERE userType = 'SERVICE'
        )
    """))
    print(f"Restored {result2.rowcount} user_media_access_uuid records")
    
    print("Downgrade completed - deprecated columns restored!")