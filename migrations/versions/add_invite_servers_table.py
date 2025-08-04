"""Add invite_servers many-to-many table

Revision ID: add_invite_servers
Revises: c697c54271f9
Create Date: 2025-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_invite_servers'
down_revision = 'c697c54271f9'
branch_labels = None
depends_on = None


def upgrade():
    # Create the invite_servers many-to-many table
    op.create_table('invite_servers',
        sa.Column('invite_id', sa.Integer(), nullable=False),
        sa.Column('server_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['invite_id'], ['invites.id'], ),
        sa.ForeignKeyConstraint(['server_id'], ['media_servers.id'], ),
        sa.PrimaryKeyConstraint('invite_id', 'server_id')
    )
    
    # Migrate existing data from server_id to the new many-to-many table
    # This will copy any existing single server associations to the new table
    connection = op.get_bind()
    connection.execute(sa.text("""
        INSERT INTO invite_servers (invite_id, server_id)
        SELECT id, server_id 
        FROM invites 
        WHERE server_id IS NOT NULL
    """))


def downgrade():
    # Drop the invite_servers table
    op.drop_table('invite_servers')