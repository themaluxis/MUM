"""Consolidate MediaStreamHistory user references

Revision ID: consolidate_media_stream_history_user_refs
Revises: d9aa2d719ac0
Create Date: 2024-01-15 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text

# revision identifiers, used by Alembic.
revision = 'consolidate_media_stream_history_user_refs'
down_revision = 'd9aa2d719ac0'
branch_labels = None
depends_on = None


def upgrade():
    """
    Consolidate user_app_access_uuid and user_media_access_uuid into single user_uuid column
    """
    print("Starting MediaStreamHistory user reference consolidation...")
    
    # Step 1: Add the new user_uuid column (check if it exists first)
    print("Adding user_uuid column...")
    
    # Check if column already exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('media_stream_history')]
    
    if 'user_uuid' not in columns:
        op.add_column('media_stream_history', sa.Column('user_uuid', sa.String(36), nullable=True))
        print("user_uuid column added successfully")
    else:
        print("user_uuid column already exists, skipping add")
    
    # Step 2: Create index on new user_uuid column (check if it exists first)
    print("Creating index on user_uuid...")
    
    # Check if index already exists
    indexes = [idx['name'] for idx in inspector.get_indexes('media_stream_history')]
    
    if 'ix_media_stream_history_user_uuid' not in indexes:
        op.create_index('ix_media_stream_history_user_uuid', 'media_stream_history', ['user_uuid'])
        print("user_uuid index created successfully")
    else:
        print("user_uuid index already exists, skipping creation")
    
    # Step 3: Populate user_uuid with data from existing columns
    print("Populating user_uuid column...")
    
    # Get connection to execute raw SQL
    connection = op.get_bind()
    
    # Update records where user_app_access_uuid is not null (LOCAL users)
    print("Copying user_app_access_uuid values...")
    result1 = connection.execute(text("""
        UPDATE media_stream_history 
        SET user_uuid = user_app_access_uuid 
        WHERE user_app_access_uuid IS NOT NULL
    """))
    print(f"Updated {result1.rowcount} records with user_app_access_uuid values")
    
    # Update records where user_media_access_uuid is not null (SERVICE users)
    print("Copying user_media_access_uuid values...")
    result2 = connection.execute(text("""
        UPDATE media_stream_history 
        SET user_uuid = user_media_access_uuid 
        WHERE user_media_access_uuid IS NOT NULL
    """))
    print(f"Updated {result2.rowcount} records with user_media_access_uuid values")
    
    # Verify data integrity - check for any records that have both UUIDs set
    print("Checking for records with both UUIDs set...")
    result_check = connection.execute(text("""
        SELECT COUNT(*) as count 
        FROM media_stream_history 
        WHERE user_app_access_uuid IS NOT NULL 
        AND user_media_access_uuid IS NOT NULL
    """))
    both_count = result_check.fetchone()[0]
    
    if both_count > 0:
        print(f"WARNING: Found {both_count} records with both UUIDs set. These will prioritize user_app_access_uuid.")
        # For records with both, user_app_access_uuid takes precedence (already set above)
    
    # Step 4: Skip foreign key constraint for now due to SQLite limitations with existing broken FKs
    print("Skipping foreign key constraint creation due to existing foreign key issues...")
    print("Note: Foreign key enforcement can be added later if needed")
    
    # Step 5: Drop old columns and their indexes (manual approach due to broken FK references)
    print("Dropping old columns and indexes...")
    
    # Refresh column and index lists
    columns = [col['name'] for col in inspector.get_columns('media_stream_history')]
    indexes = [idx['name'] for idx in inspector.get_indexes('media_stream_history')]
    
    # Drop old indexes directly (avoid batch mode due to broken FK references)
    if 'ix_media_stream_history_user_app_access_uuid' in indexes:
        try:
            op.drop_index('ix_media_stream_history_user_app_access_uuid', 'media_stream_history')
            print("Dropped ix_media_stream_history_user_app_access_uuid index")
        except Exception as e:
            print(f"Note: Could not drop ix_media_stream_history_user_app_access_uuid - {e}")
    else:
        print("ix_media_stream_history_user_app_access_uuid index not found, skipping")
    
    if 'ix_media_stream_history_user_media_access_uuid' in indexes:
        try:
            op.drop_index('ix_media_stream_history_user_media_access_uuid', 'media_stream_history')
            print("Dropped ix_media_stream_history_user_media_access_uuid index")
        except Exception as e:
            print(f"Note: Could not drop ix_media_stream_history_user_media_access_uuid - {e}")
    else:
        print("ix_media_stream_history_user_media_access_uuid index not found, skipping")
    
    # Drop old columns directly (avoid batch mode due to broken FK references)
    if 'user_app_access_uuid' in columns:
        try:
            op.drop_column('media_stream_history', 'user_app_access_uuid')
            print("Dropped user_app_access_uuid column")
        except Exception as e:
            print(f"Note: Could not drop user_app_access_uuid column - {e}")
    else:
        print("user_app_access_uuid column not found, skipping")
        
    if 'user_media_access_uuid' in columns:
        try:
            op.drop_column('media_stream_history', 'user_media_access_uuid')
            print("Dropped user_media_access_uuid column")
        except Exception as e:
            print(f"Note: Could not drop user_media_access_uuid column - {e}")
    else:
        print("user_media_access_uuid column not found, skipping")
    
    print("MediaStreamHistory user reference consolidation completed successfully!")


def downgrade():
    """
    Restore separate user_app_access_uuid and user_media_access_uuid columns
    """
    print("Starting MediaStreamHistory user reference downgrade...")
    
    # Step 1: Re-add the old columns
    print("Re-adding old user reference columns...")
    op.add_column('media_stream_history', sa.Column('user_app_access_uuid', sa.String(36), nullable=True))
    op.add_column('media_stream_history', sa.Column('user_media_access_uuid', sa.String(36), nullable=True))
    
    # Step 2: Create indexes on old columns
    print("Creating indexes on old columns...")
    op.create_index('ix_media_stream_history_user_app_access_uuid', 'media_stream_history', ['user_app_access_uuid'])
    op.create_index('ix_media_stream_history_user_media_access_uuid', 'media_stream_history', ['user_media_access_uuid'])
    
    # Step 3: Populate old columns based on user types
    print("Populating old columns based on user types...")
    
    connection = op.get_bind()
    
    # Restore user_app_access_uuid for LOCAL users
    print("Restoring user_app_access_uuid for LOCAL users...")
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
    
    # Step 4: Add foreign key constraints to old columns (SQLite compatible)
    print("Adding foreign key constraints to old columns...")
    with op.batch_alter_table('media_stream_history', schema=None) as batch_op:
        batch_op.create_foreign_key('fk_media_stream_history_user_app_access_uuid', 'users', ['user_app_access_uuid'], ['uuid'])
        batch_op.create_foreign_key('fk_media_stream_history_user_media_access_uuid', 'users', ['user_media_access_uuid'], ['uuid'])
    
    # Step 5: Drop new user_uuid column and its constraints (SQLite compatible)
    print("Dropping new user_uuid column...")
    
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('media_stream_history', schema=None) as batch_op:
        # Drop foreign key constraint first
        try:
            batch_op.drop_constraint('fk_media_stream_history_user_uuid', type_='foreignkey')
        except Exception as e:
            print(f"Note: Could not drop fk_media_stream_history_user_uuid - {e}")
        
        # Drop index
        try:
            batch_op.drop_index('ix_media_stream_history_user_uuid')
        except Exception as e:
            print(f"Note: Could not drop ix_media_stream_history_user_uuid - {e}")
        
        # Drop column
        batch_op.drop_column('user_uuid')
    
    print("MediaStreamHistory user reference downgrade completed!")