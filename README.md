# Multimedia User Management (MUM)

[![Docker Image CI](https://github.com/MrRobotjs/MUM/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/MrRobotjs/MUM/actions/workflows/docker-publish.yml)
[![GitHub stars](https://img.shields.io/github/stars/MrRobotjs/MUM.svg?style=social&label=Star&maxAge=2592000)](https://github.com/MrRobotjs/MUM/stargazers/)
[![](https://dcbadge.limes.pink/api/server/https://discord.gg/QGHQWpGNgX)](https://discord.gg/QGHQWpGNgX)
[![PayPal](https://img.shields.io/badge/PayPal-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://www.paypal.com/donate/?business=D7BJAJ9ZY4GRC&no_recurring=0&currency_code=USD)

MUM (Multimedia User Management) is a powerful, self-hosted web application designed to centralize and simplify user management across a variety of media servers. Featuring a modular plugin system, MUM allows administrators to seamlessly manage users, invitations, and server access from a single, user-friendly interface.

| Dashboard | Invites |
| :---: | :---: |
| ![image](https://github.com/user-attachments/assets/18db06e2-66c2-4e15-a010-59dc5499761d) | ![image](https://github.com/user-attachments/assets/dcb72d92-94f1-4246-aa81-e6163e3ff763) |
| Users | Streaming |
| ![image](https://github.com/user-attachments/assets/77c35536-62fd-44e3-9356-5cd6156fcf26) | ![image](https://github.com/user-attachments/assets/755f6dec-c839-4145-9d08-67c2de91303d) |

## Key Features

*   **Plugin-Based Architecture:**
    *   Easily enable, disable, and configure various media services.
    *   Core plugins for popular services are included out-of-the-box.
    *   Extensible system allows for the creation and installation of third-party plugins.
*   **Supported Services (via Core Plugins):**
    *   **Plex:** Full-featured user management, library sharing, and session monitoring.
    *   **Jellyfin & Emby:** Comprehensive user and library management.
    *   **Kavita & Komga:** Manga and comic server user management.
    *   **AudiobookShelf:** Audiobook server user management.
    *   **RomM:** Retro gaming server user management.
*   **Unified User Management:**
    *   View and manage users from all connected services in one place.
    *   Sync users from your media servers to the MUM database.
    *   Edit user details, notes, and library access permissions.
    *   Mass edit capabilities to update libraries or delete multiple users at once.
*   **Advanced Invite System:**
    *   Create flexible, token-based invite links.
    *   Set expiration dates and usage limits for invites.
    *   Specify libraries and permissions to be granted upon invite acceptance.
    *   Set membership durations for temporary access, with automatic user removal.
*   **Discord Integration:**
    *   Allow users to link their Discord account via OAuth.
    *   Optionally require Discord linking and server membership to accept an invite.
    *   (Future) Advanced bot features for role-based invites and server activity monitoring.
*   **Dashboard & Monitoring:**
    *   At-a-glance dashboard with key statistics for all connected services.
    *   View active streams with details like user, player, media, and progress.
    *   Terminate active streams directly from the dashboard.
*   **Lifecycle Management:**
    *   Preview and purge inactive users based on configurable criteria.
    *   Whitelist users to protect them from automated purges.
*   **Modern UI & Security:**
    *   Clean, responsive interface built with Flask, Tailwind CSS, and HTMX.
    *   Secure admin login with local accounts or Plex SSO.
    *   Multi-step setup wizard for easy initial configuration.

## Docker Deployment

The easiest way to deploy MUM is with Docker.

### Docker Compose

1.  **Create a `docker-compose.yml` file:**
    ```yaml
    services:
      mum:
        image: ghcr.io/mrrobotjs/mum:latest
        container_name: mum
        restart: unless-stopped
        ports:
          - "5699:5000" # <host_port>:<container_port>
        volumes:
          - ./multimediausermanager:/app/instance
        environment:
          - TZ=America/New_York # REQUIRED: Set your local timezone
          - PUID=1000 # Optional: User ID for file permissions
          - PGID=1000 # Optional: Group ID for file permissions
    ```

2.  **Prepare Host Directory:**
    Create the directory on your host machine that you specified in the `volumes` section.
    ```bash
    mkdir ./multimediausermanager
    ```
    This directory will store the database and other persistent data.

3.  **Customize and Run:**
    *   Adjust the port mapping (`5699:5000`) if the host port is in use.
    *   Set the `TZ` environment variable to your local timezone (e.g., `Europe/London`).
    *   Run the application:
        ```bash
        docker-compose up -d
        ```

4.  **Initial Setup:**
    *   Access MUM in your browser at `http://<your_host_ip>:<host_port>` (e.g., `http://localhost:5699`).
    *   Follow the on-screen setup wizard to create an admin account, configure your media servers, and set the application's base URL.

## Configuration

Most application settings are configurable through the web UI after the initial setup. This includes:

*   **General:** Application Name and Base URL.
*   **Plugins:** Enable, disable, and configure all media server integrations.
*   **Discord:** Set up OAuth credentials, Bot Token, and feature toggles.
*   **Admins & Roles:** Manage administrator accounts and their permissions.
*   **Advanced:** Regenerate the application's secret key and view raw settings.

## Plugin Development

MUM's modular nature makes it easy to extend. If you're interested in adding support for a new media service, you can create your own plugin. For more information, see the [Plugin Development Guide](PLUGIN_DEVELOPMENT_GUIDE.md).

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request or open an Issue.