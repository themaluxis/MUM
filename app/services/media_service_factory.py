# File: app/services/media_service_factory.py
from typing import Dict, Any, Optional
from app.models_media_services import ServiceType, MediaServer
from app.services.base_media_service import BaseMediaService
from app.services.plugin_manager import plugin_manager

class MediaServiceFactory:
    """Plugin-aware factory class for creating media service instances"""
    
    @classmethod
    def create_service(cls, server_config: Dict[str, Any]) -> Optional[BaseMediaService]:
        """Create a media service instance based on server configuration using plugins"""
        from flask import current_app
        
        service_type = server_config.get('service_type')
        current_app.logger.debug(f"MediaServiceFactory - Creating service for type: {service_type} (type: {type(service_type)})")
        
        # Handle both string and enum service types
        if isinstance(service_type, ServiceType):
            plugin_id = service_type.value
        elif isinstance(service_type, str):
            plugin_id = service_type
        else:
            current_app.logger.error(f"MediaServiceFactory - Invalid service_type: {service_type} (type: {type(service_type)})")
            return None
        
        current_app.logger.debug(f"MediaServiceFactory - Looking for plugin_id: {plugin_id}")
        
        # Get plugin class from plugin manager
        service_class = plugin_manager.get_plugin_class(plugin_id)
        current_app.logger.debug(f"MediaServiceFactory - Plugin class from manager: {service_class}")
        
        # If plugin isn't enabled but exists, try to load it temporarily for testing
        if not service_class:
            from app.models_plugins import Plugin
            try:
                plugin = Plugin.query.filter_by(plugin_id=plugin_id).first()
                current_app.logger.debug(f"MediaServiceFactory - Found plugin in DB: {plugin}")
                if plugin:
                    service_class = plugin_manager._load_plugin(plugin)
                    current_app.logger.debug(f"MediaServiceFactory - Loaded plugin class: {service_class}")
            except Exception as e:
                current_app.logger.error(f"MediaServiceFactory - Error loading plugin: {e}")
                pass
        
        if not service_class:
            current_app.logger.error(f"MediaServiceFactory - No service class found for plugin_id: {plugin_id}")
            return None
        
        try:
            service_instance = service_class(server_config)
            current_app.logger.debug(f"MediaServiceFactory - Successfully created service instance: {service_instance}")
            return service_instance
        except Exception as e:
            current_app.logger.error(f"MediaServiceFactory - Error creating service instance: {e}")
            return None
    
    @classmethod
    def create_service_from_db(cls, media_server: MediaServer) -> Optional[BaseMediaService]:
        """Create a media service instance from a database MediaServer object"""
        server_config = {
            'id': media_server.id,
            'name': media_server.name,
            'service_type': media_server.service_type,
            'url': media_server.url,
            'api_key': media_server.api_key,
            'username': media_server.username,
            'password': media_server.password,
            'config': media_server.config or {}
        }
        
        return cls.create_service(server_config)
    
    @classmethod
    def get_supported_services(cls) -> Dict[str, str]:
        """Get a dictionary of enabled service types and their display names"""
        enabled_plugins = plugin_manager.get_enabled_plugins()
        return {plugin.plugin_id: plugin.name for plugin in enabled_plugins}
    
    @classmethod
    def get_service_features(cls, plugin_id: str) -> Dict[str, bool]:
        """Get supported features for a plugin"""
        plugin_info = plugin_manager.get_plugin_info(plugin_id)
        if not plugin_info:
            return {}
        
        # Convert list of features to dict with True values
        supported_features = plugin_info.get('supported_features', [])
        return {feature: True for feature in supported_features}