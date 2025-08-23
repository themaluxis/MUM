"""Add uuid columns to user tables

Revision ID: add_global_id_uuids
Revises: add_user_media_access_stream
Create Date: 2025-01-20 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_global_id_uuids'
down_revision = 'add_user_media_access_stream'
branch_labels = None
depends_on = None


def upgrade():
    # Add uuid columns with UUID default
    # For PostgreSQL, use UUID type with gen_random_uuid()
    # For SQLite, use String(36) with Python uuid4()
    
    # Check if we're using PostgreSQL or SQLite
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # PostgreSQL implementation
        op.add_column('user_app_access', sa.Column('uuid', postgresql.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()')))
        op.add_column('user_media_access', sa.Column('uuid', postgresql.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()')))
        
        # Add unique constraints
        op.create_unique_constraint('uq_user_app_access_uuid', 'user_app_access', ['uuid'])
        op.create_unique_constraint('uq_user_media_access_uuid', 'user_media_access', ['uuid'])
        
        # Create indexes for performance
        op.create_index('idx_user_app_access_uuid', 'user_app_access', ['uuid'])
        op.create_index('idx_user_media_access_uuid', 'user_media_access', ['uuid'])
    else:
        # SQLite implementation - use batch mode for constraint operations
        import uuid
        from sqlalchemy import text
        
        # Use batch mode for user_app_access
        with op.batch_alter_table('user_app_access', schema=None) as batch_op:
            batch_op.add_column(sa.Column('uuid', sa.String(36), nullable=True))
        
        # Generate UUIDs for existing user_app_access records
        connection = op.get_bind()
        result = connection.execute(text("SELECT id FROM user_app_access"))
        for row in result:
            connection.execute(
                text("UPDATE user_app_access SET uuid = :uuid WHERE id = :id"),
                {"uuid": str(uuid.uuid4()), "id": row[0]}
            )
        
        # Make uuid NOT NULL and add constraints
        with op.batch_alter_table('user_app_access', schema=None) as batch_op:
            batch_op.alter_column('uuid', nullable=False)
            batch_op.create_unique_constraint('uq_user_app_access_uuid', ['uuid'])
            batch_op.create_index('idx_user_app_access_uuid', ['uuid'])
        
        # Use batch mode for user_media_access
        with op.batch_alter_table('user_media_access', schema=None) as batch_op:
            batch_op.add_column(sa.Column('uuid', sa.String(36), nullable=True))
        
        # Generate UUIDs for existing user_media_access records
        result = connection.execute(text("SELECT id FROM user_media_access"))
        for row in result:
            connection.execute(
                text("UPDATE user_media_access SET uuid = :uuid WHERE id = :id"),
                {"uuid": str(uuid.uuid4()), "id": row[0]}
            )
        
        # Make uuid NOT NULL and add constraints
        with op.batch_alter_table('user_media_access', schema=None) as batch_op:
            batch_op.alter_column('uuid', nullable=False)
            batch_op.create_unique_constraint('uq_user_media_access_uuid', ['uuid'])
            batch_op.create_index('idx_user_media_access_uuid', ['uuid'])


def downgrade():
    # Check if we're using PostgreSQL or SQLite
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # PostgreSQL implementation
        op.drop_index('idx_user_media_access_uuid', table_name='user_media_access')
        op.drop_index('idx_user_app_access_uuid', table_name='user_app_access')
        
        # Remove unique constraints
        op.drop_constraint('uq_user_media_access_uuid', 'user_media_access', type_='unique')
        op.drop_constraint('uq_user_app_access_uuid', 'user_app_access', type_='unique')
        
        # Remove columns
        op.drop_column('user_media_access', 'uuid')
        op.drop_column('user_app_access', 'uuid')
    else:
        # SQLite implementation - use batch mode
        with op.batch_alter_table('user_media_access', schema=None) as batch_op:
            batch_op.drop_index('idx_user_media_access_uuid')
            batch_op.drop_constraint('uq_user_media_access_uuid', type_='unique')
            batch_op.drop_column('uuid')
        
        with op.batch_alter_table('user_app_access', schema=None) as batch_op:
            batch_op.drop_index('idx_user_app_access_uuid')
            batch_op.drop_constraint('uq_user_app_access_uuid', type_='unique')
            batch_op.drop_column('uuid')