"""Fix history_logs column name from user_app_access_id to local_user_id

Revision ID: fix_history_logs_column_name
Revises: cleanup_deprecated_user_tables
Create Date: 2025-01-15 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'fix_history_logs_column_name'
down_revision = 'cleanup_deprecated_user_tables'
branch_labels = None
depends_on = None


def upgrade():
    """
    Rename user_app_access_id column to local_user_id in history_logs table
    """
    print("Starting history_logs column rename...")
    
    # Check if columns exist before trying to rename
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('history_logs')]
    
    print(f"Current columns in history_logs: {columns}")
    
    if 'user_app_access_id' in columns and 'local_user_id' not in columns:
        # Drop the old foreign key constraint first (if it exists)
        try:
            foreign_keys = inspector.get_foreign_keys('history_logs')
            old_fk_name = None
            for fk in foreign_keys:
                if 'user_app_access_id' in fk['constrained_columns']:
                    old_fk_name = fk['name']
                    break
            
            if old_fk_name:
                print(f"Dropping old foreign key constraint: {old_fk_name}")
                op.drop_constraint(old_fk_name, 'history_logs', type_='foreignkey')
        except Exception as e:
            print(f"Note: Could not drop old foreign key constraint - {e}")
        
        # Use raw SQL to rename the column (SQLite PRAGMA approach)
        print("Renaming column using direct SQL...")
        conn.execute(sa.text("""
            CREATE TABLE history_logs_new (
                id INTEGER PRIMARY KEY,
                timestamp DATETIME NOT NULL,
                event_type VARCHAR(50) NOT NULL,
                message TEXT NOT NULL,
                details TEXT,
                owner_id INTEGER,
                local_user_id INTEGER,
                invite_id INTEGER,
                FOREIGN KEY (owner_id) REFERENCES users(id),
                FOREIGN KEY (local_user_id) REFERENCES users(id),
                FOREIGN KEY (invite_id) REFERENCES invites(id)
            )
        """))
        
        # Copy data from old table to new table
        conn.execute(sa.text("""
            INSERT INTO history_logs_new (id, timestamp, event_type, message, details, owner_id, local_user_id, invite_id)
            SELECT id, timestamp, event_type, message, details, owner_id, user_app_access_id, invite_id
            FROM history_logs
        """))
        
        # Drop old table and rename new table
        conn.execute(sa.text("DROP TABLE history_logs"))
        conn.execute(sa.text("ALTER TABLE history_logs_new RENAME TO history_logs"))
        
        # Create indexes
        conn.execute(sa.text("CREATE INDEX ix_history_logs_event_type ON history_logs (event_type)"))
        
        print("Renamed user_app_access_id to local_user_id")
    elif 'local_user_id' in columns:
        print("local_user_id column already exists, skipping rename")
    else:
        print("Neither user_app_access_id nor local_user_id found, adding local_user_id")
        op.add_column('history_logs', sa.Column('local_user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True))
    
    print("History logs column rename completed successfully!")


def downgrade():
    """
    Rename local_user_id column back to user_app_access_id in history_logs table
    """
    print("Starting history_logs column rename downgrade...")
    
    # Use batch mode for SQLite compatibility
    with op.batch_alter_table('history_logs', schema=None) as batch_op:
        # Rename back to original name
        batch_op.alter_column('local_user_id', new_column_name='user_app_access_id')
    
    print("History logs column rename downgrade completed!")