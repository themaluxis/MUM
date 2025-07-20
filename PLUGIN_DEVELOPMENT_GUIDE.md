# MUM Plugin Development Guide

This guide will help you create custom plugins for MUM (Multimedia User Management) to integrate with new media services.

## Overview

MUM uses a plugin-based architecture that allows you to add support for new media services without modifying the core application. Each plugin is a self-contained module that implements the `BaseMediaService` interface.

## Plugin Structure

A MUM plugin consists of:

1. **Python module** - Contains your service implementation
2. **Manifest file** - `plugin.json` with metadata and configuration
3. **Optional dependencies** - Additional Python packages if needed

## Quick Start

### 1. Create Plugin Directory

```
my_service_plugin/
├── plugin.json          # Plugin manifest
├── my_service.py         # Main service implementation
├── __init__.py          # Python package init
└── README.md            # Documentation
```

### 2. Implement Service Class

Create your service class by extending `BaseMediaService`:

```python
from app.services.base_media_service import BaseMediaService
from typing import List, Dict, Any, Tuple

class MyServiceMediaService(BaseMediaService):
    @property
    def service_type(self):
        return "my_service"
    
    def test_connection(self) -> Tuple[bool, str]:
        # Test connectivity to your service
        pass
    
    def get_libraries(self) -> List[Dict[str, Any]]:
        # Fetch available libraries
        pass
    
    def get_users(self) -> List[Dict[str, Any]]:
        # Fetch users from your service
        pass
    
    # ... implement other required methods
```

### 3. Create Plugin Manifest

Create `plugin.json` with your plugin metadata:

```json
{
  "plugin_id": "my_service",
  "name": "My Media Service",
  "description": "Integration with My Media Service",
  "version": "1.0.0",
  "author": "Your Name",
  "module_path": "my_service",
  "service_class": "MyServiceMediaService",
  "supported_features": [
    "user_management",
    "library_access"
  ]
}
```

### 4. Package and Install

1. Create a ZIP archive of your plugin directory
2. Upload through MUM's admin interface: **Admin > Plugins > Install Plugin**
3. Enable your plugin from the plugin list

## Required Methods

Your service class must implement these abstract methods:

### Connection Management

```python
def test_connection(self) -> Tuple[bool, str]:
    """
    Test connection to your service.
    
    Returns:
        Tuple of (success: bool, message: str)
    """
```

### Library Management

```python
def get_libraries(self) -> List[Dict[str, Any]]:
    """
    Get all libraries from your service.
    
    Returns:
        List of library dictionaries with keys:
        - id: Library identifier
        - name: Display name
        - type: Library type (movies, shows, books, etc.)
        - item_count: Number of items (optional)
        - external_id: Service-specific ID
    """
```

### User Management

```python
def get_users(self) -> List[Dict[str, Any]]:
    """
    Get all users from your service.
    
    Returns:
        List of user dictionaries with keys:
        - id: User identifier
        - uuid: Unique user ID (can be same as id)
        - username: Display name
        - email: Email address (optional)
        - thumb: Avatar URL (optional)
        - is_home_user: Boolean (service-specific)
        - library_ids: List of accessible library IDs
        - is_admin: Boolean
    """

def create_user(self, username: str, email: str, password: str = None, **kwargs) -> Dict[str, Any]:
    """
    Create a new user in your service.
    
    Args:
        username: User's username
        email: User's email
        password: User's password (optional)
        **kwargs: Additional parameters like library_ids, allow_downloads
    
    Returns:
        Dictionary with keys:
        - success: Boolean
        - user_id: Created user's ID
        - username: Username
        - email: Email
    """

def update_user_access(self, user_id: str, library_ids: List[str] = None, **kwargs) -> bool:
    """
    Update user's library access and permissions.
    
    Args:
        user_id: User identifier
        library_ids: List of library IDs to grant access to
        **kwargs: Additional parameters like allow_downloads
    
    Returns:
        Boolean indicating success
    """

def delete_user(self, user_id: str) -> bool:
    """
    Delete/remove user from your service.
    
    Args:
        user_id: User identifier
    
    Returns:
        Boolean indicating success
    """
```

### Session Management

```python
def get_active_sessions(self) -> List[Dict[str, Any]]:
    """
    Get currently active streaming sessions.
    
    Returns:
        List of session dictionaries with keys:
        - session_id: Session identifier
        - user_id: User identifier
        - username: User's display name
        - media_title: Currently playing media
        - media_type: Type of media (movie, episode, etc.)
        - player: Player/client name
        - platform: Platform/device type
        - state: Playback state (playing, paused, etc.)
        - progress_percent: Playback progress (0-100)
        - ip_address: Client IP address
        - is_lan: Boolean indicating local network
    """

def terminate_session(self, session_id: str, reason: str = None) -> bool:
    """
    Terminate an active streaming session.
    
    Args:
        session_id: Session identifier
        reason: Optional reason message
    
    Returns:
        Boolean indicating success
    """
```

## Plugin Manifest Reference

The `plugin.json` file contains metadata about your plugin:

```json
{
  "plugin_id": "unique_identifier",
  "name": "Display Name",
  "description": "Brief description of your plugin",
  "version": "1.0.0",
  "author": "Your Name",
  "license": "MIT",
  "homepage": "https://github.com/yourname/plugin",
  "repository": "https://github.com/yourname/plugin.git",
  
  "module_path": "python_module_name",
  "service_class": "YourServiceClass",
  
  "supported_features": [
    "user_management",
    "library_access",
    "active_sessions",
    "downloads",
    "transcoding",
    "sharing",
    "invitations"
  ],
  
  "min_mum_version": "1.0.0",
  "max_mum_version": null,
  
  "python_requirements": [
    "requests>=2.25.0",
    "xmltodict>=0.12.0"
  ],
  
  "config_schema": {
    "type": "object",
    "properties": {
      "timeout": {
        "type": "integer",
        "default": 10,
        "minimum": 5,
        "maximum": 60
      }
    }
  },
  
  "default_config": {
    "timeout": 10
  }
}
```

### Manifest Fields

- **plugin_id**: Unique identifier (lowercase, no spaces)
- **name**: Human-readable plugin name
- **description**: Brief description of functionality
- **version**: Semantic version (e.g., "1.0.0")
- **author**: Plugin author name
- **license**: License type (e.g., "MIT", "GPL-3.0")
- **homepage**: Plugin homepage URL
- **repository**: Source code repository URL
- **module_path**: Python module name containing your service class
- **service_class**: Name of your service class
- **supported_features**: List of features your service supports
- **min_mum_version**: Minimum required MUM version
- **max_mum_version**: Maximum supported MUM version (optional)
- **python_requirements**: List of required Python packages
- **config_schema**: JSON schema for configuration validation
- **default_config**: Default configuration values

## Supported Features

Declare which features your service supports:

- **user_management**: Create, update, delete users
- **library_access**: Manage user library permissions
- **active_sessions**: Monitor streaming sessions
- **downloads**: Support for download permissions
- **transcoding**: Support for transcoding settings
- **sharing**: Support for user-to-user sharing
- **invitations**: Support for invite-based user creation

## Configuration Schema

Define configuration options for your plugin using JSON Schema:

```json
{
  "config_schema": {
    "type": "object",
    "properties": {
      "timeout": {
        "type": "integer",
        "default": 10,
        "minimum": 5,
        "maximum": 60,
        "description": "Request timeout in seconds"
      },
      "verify_ssl": {
        "type": "boolean",
        "default": true,
        "description": "Verify SSL certificates"
      },
      "api_version": {
        "type": "string",
        "enum": ["v1", "v2"],
        "default": "v2",
        "description": "API version to use"
      }
    }
  }
}
```

## Helper Methods

The `BaseMediaService` class provides helpful methods:

```python
# Logging
self.log_info("Information message")
self.log_warning("Warning message")
self.log_error("Error message", exc_info=True)

# Feature checking
if self.supports_feature('downloads'):
    # Handle download permissions

# Configuration access
timeout = self.config.get('timeout', 10)
```

## Error Handling

Always handle errors gracefully:

```python
def get_users(self) -> List[Dict[str, Any]]:
    try:
        response = self._make_request('users')
        return self._parse_users(response)
    except requests.exceptions.ConnectionError:
        self.log_error("Connection failed")
        return []
    except Exception as e:
        self.log_error(f"Unexpected error: {e}", exc_info=True)
        return []
```

## Testing Your Plugin

1. **Unit Tests**: Test individual methods with mock data
2. **Integration Tests**: Test against a real service instance
3. **MUM Integration**: Test within MUM environment

Example test structure:

```python
import unittest
from unittest.mock import Mock, patch
from my_service import MyServiceMediaService

class TestMyServicePlugin(unittest.TestCase):
    def setUp(self):
        self.config = {
            'url': 'http://test-server',
            'api_key': 'test-key'
        }
        self.service = MyServiceMediaService(self.config)
    
    @patch('requests.get')
    def test_connection(self, mock_get):
        mock_get.return_value.json.return_value = {'status': 'ok'}
        success, message = self.service.test_connection()
        self.assertTrue(success)
```

## Best Practices

### 1. Error Handling
- Always catch and log exceptions
- Return empty lists/dicts instead of None
- Provide meaningful error messages

### 2. API Requests
- Use timeouts for all requests
- Implement retry logic for transient failures
- Respect rate limits

### 3. Data Validation
- Validate all input parameters
- Sanitize data from external APIs
- Use type hints for better code clarity

### 4. Configuration
- Provide sensible defaults
- Validate configuration on startup
- Document all configuration options

### 5. Logging
- Log important operations
- Use appropriate log levels
- Include context in error messages

## Example Plugins

### Simple REST API Service

```python
class SimpleAPIService(BaseMediaService):
    @property
    def service_type(self):
        return "simple_api"
    
    def _make_request(self, endpoint, method='GET', data=None):
        url = f"{self.url}/api/{endpoint}"
        headers = {'Authorization': f'Bearer {self.api_key}'}
        
        response = requests.request(method, url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    
    def test_connection(self):
        try:
            self._make_request('status')
            return True, "Connected successfully"
        except Exception as e:
            return False, str(e)
```

### Service with Basic Auth

```python
class BasicAuthService(BaseMediaService):
    def _get_auth(self):
        import base64
        credentials = base64.b64encode(
            f"{self.username}:{self.password}".encode()
        ).decode()
        return {'Authorization': f'Basic {credentials}'}
```

## Troubleshooting

### Common Issues

1. **Import Errors**: Check module path in manifest
2. **Connection Failures**: Verify URL and authentication
3. **Missing Methods**: Ensure all abstract methods are implemented
4. **Plugin Not Loading**: Check logs for detailed error messages

### Debug Mode

Enable debug logging to see detailed plugin loading information:

```python
import logging
logging.getLogger('app.services.plugin_manager').setLevel(logging.DEBUG)
```

## Publishing Your Plugin

1. **Test Thoroughly**: Ensure compatibility with different service versions
2. **Document Well**: Provide clear installation and configuration instructions
3. **Version Properly**: Use semantic versioning
4. **Share**: Consider sharing with the MUM community

## Support

- **GitHub Issues**: Report bugs and request features
- **Community Forum**: Get help from other developers
- **Documentation**: Check the latest API documentation

## License

When creating plugins, choose an appropriate license and include it in your plugin package. Popular choices include MIT, Apache 2.0, and GPL-3.0.