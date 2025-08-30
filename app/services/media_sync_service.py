"""
Media Sync Service
Handles syncing media items from external services to local database for faster access
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from flask import current_app
from sqlalchemy import and_, or_
from app.extensions import db
from app.models_media_services import MediaItem, MediaLibrary, MediaServer
from app.services.media_service_factory import MediaServiceFactory


class MediaSyncService:
    """Service for syncing media items from external services to local database"""
    
    @staticmethod
    def sync_library_content(library_id: int, force_full_sync: bool = False) -> Dict[str, Any]:
        """
        Sync content for a specific library
        
        Args:
            library_id: ID of the library to sync
            force_full_sync: If True, sync all items regardless of last sync time
            
        Returns:
            Dict with sync results
        """
        try:
            library = MediaLibrary.query.get(library_id)
            if not library:
                return {'success': False, 'error': 'Library not found'}
            
            current_app.logger.info(f"Starting media sync for library: {library.name}")
            
            # Create service instance
            service = MediaServiceFactory.create_service_from_db(library.server)
            if not service:
                return {'success': False, 'error': 'Could not create service instance'}
            
            # Check if service supports get_library_content
            if not hasattr(service, 'get_library_content'):
                return {'success': False, 'error': 'Service does not support library content retrieval'}
            
            # Get all items from the service with timeout protection
            all_items = []
            page = 1
            per_page = 50  # Smaller batches to prevent timeouts
            max_pages = 100  # Limit total pages to prevent infinite loops
            
            while page <= max_pages:
                try:
                    current_app.logger.debug(f"Syncing library {library.name}, page {page}")
                    content_data = service.get_library_content(library.external_id, page=page, per_page=per_page)
                    
                    # Handle error responses
                    if content_data.get('error'):
                        current_app.logger.warning(f"API error on page {page}: {content_data['error']}")
                        break
                    
                    items = content_data.get('items', [])
                    
                    if not items:
                        break
                        
                    all_items.extend(items)
                    current_app.logger.debug(f"Retrieved {len(items)} items from page {page}, total so far: {len(all_items)}")
                    
                    # Check if we've got all items
                    if len(items) < per_page:
                        break
                        
                    page += 1
                    
                    # Add a small delay to prevent overwhelming the API
                    import time
                    time.sleep(0.1)
                    
                except Exception as e:
                    current_app.logger.error(f"Error fetching page {page} for library {library.name}: {e}")
                    # Continue with partial data rather than failing completely
                    break
            
            current_app.logger.info(f"Retrieved {len(all_items)} items from {library.name}")
            
            # Sync items to database
            sync_results = MediaSyncService._sync_items_to_db(library, all_items)
            
            # Update library last sync time
            library.last_scanned = datetime.utcnow()
            db.session.add(library)
            db.session.commit()
            
            current_app.logger.info(f"Completed sync for library {library.name}: {sync_results}")
            
            return {
                'success': True,
                'library_name': library.name,
                'total_items': len(all_items),
                **sync_results
            }
            
        except Exception as e:
            current_app.logger.error(f"Error syncing library {library_id}: {e}")
            db.session.rollback()
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def _sync_items_to_db(library: MediaLibrary, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Sync items to database
        
        Args:
            library: MediaLibrary instance
            items: List of item dictionaries from service
            
        Returns:
            Dict with counts and details of added, updated, removed items
        """
        added_count = 0
        updated_count = 0
        added_items = []
        updated_items = []
        errors = []
        
        # Get existing items for this library
        existing_items = {item.external_id: item for item in 
                         MediaItem.query.filter_by(library_id=library.id).all()}
        
        current_external_ids = set()
        
        for item_data in items:
            try:
                external_id = str(item_data.get('id', ''))
                if not external_id:
                    continue
                    
                current_external_ids.add(external_id)
                
                # Check if item exists
                existing_item = existing_items.get(external_id)
                
                if existing_item:
                    # Update existing item
                    changes = MediaSyncService._update_media_item(existing_item, item_data)
                    if changes:
                        updated_count += 1
                        updated_items.append({
                            'title': item_data.get('title', 'Unknown Title'),
                            'type': item_data.get('type', 'unknown'),
                            'year': item_data.get('year'),
                            'changes': changes
                        })
                else:
                    # Create new item
                    new_item = MediaSyncService._create_media_item(library, item_data)
                    if new_item:
                        added_count += 1
                        added_items.append({
                            'title': item_data.get('title', 'Unknown Title'),
                            'type': item_data.get('type', 'unknown'),
                            'year': item_data.get('year')
                        })
                        
            except Exception as e:
                error_msg = f"Error processing item {item_data.get('title', 'unknown')}: {str(e)}"
                current_app.logger.warning(error_msg)
                errors.append(error_msg)
                continue
        
        # Remove items that no longer exist on the service
        items_to_remove = [item for external_id, item in existing_items.items() 
                          if external_id not in current_external_ids]
        
        removed_count = 0
        removed_items = []
        for item in items_to_remove:
            removed_items.append({
                'title': item.title,
                'type': item.item_type,
                'year': item.year
            })
            db.session.delete(item)
            removed_count += 1
        
        # Commit all changes
        try:
            db.session.commit()
            current_app.logger.info(f"Sync completed: {added_count} added, {updated_count} updated, {removed_count} removed")
        except Exception as e:
            current_app.logger.error(f"Error committing sync changes: {e}")
            db.session.rollback()
            raise
        
        return {
            'added': added_count,
            'updated': updated_count,
            'removed': removed_count,
            'added_items': added_items[:50],  # Limit to first 50 for display
            'updated_items': updated_items[:50],  # Limit to first 50 for display
            'removed_items': removed_items[:50],  # Limit to first 50 for display
            'errors': errors
        }
    
    @staticmethod
    def _create_media_item(library: MediaLibrary, item_data: Dict[str, Any]) -> Optional[MediaItem]:
        """Create a new MediaItem from service data"""
        try:
            # Extract thumbnail path (handle different service formats)
            thumb_path = None
            if item_data.get('thumb'):
                thumb_url = item_data['thumb']
                if thumb_url.startswith('/api/'):
                    # Already a proxy URL (Jellyfin or other services) - store as-is
                    thumb_path = thumb_url
                elif '/api/media/plex/images/proxy' in thumb_url and 'path=' in thumb_url:
                    # Plex proxy URL: extract path
                    thumb_path = thumb_url.split('path=')[1]
                elif thumb_url.startswith('/'):
                    # Direct path
                    thumb_path = thumb_url
            
            # Parse duration (handle different formats)
            duration = item_data.get('duration')
            if duration and isinstance(duration, str):
                try:
                    duration = int(duration)
                except ValueError:
                    duration = None
            
            # Parse added_at date
            added_at = None
            if item_data.get('added_at'):
                try:
                    if isinstance(item_data['added_at'], str):
                        added_at = datetime.fromisoformat(item_data['added_at'].replace('Z', '+00:00'))
                    else:
                        added_at = item_data['added_at']
                except (ValueError, TypeError):
                    pass
            
            media_item = MediaItem(
                library_id=library.id,
                server_id=library.server_id,
                external_id=str(item_data.get('id', '')),
                title=item_data.get('title', 'Unknown Title'),
                sort_title=item_data.get('sort_title') or item_data.get('title', 'Unknown Title'),
                item_type=item_data.get('type', 'unknown'),
                summary=item_data.get('summary') or item_data.get('plot') or item_data.get('overview'),
                year=item_data.get('year'),
                rating=item_data.get('rating'),
                duration=duration,
                thumb_path=thumb_path,
                added_at=added_at,
                last_synced=datetime.utcnow(),
                extra_metadata=item_data.get('raw_data', {})
            )
            
            db.session.add(media_item)
            return media_item
            
        except Exception as e:
            current_app.logger.error(f"Error creating media item: {e}")
            return None
    
    @staticmethod
    def _update_media_item(item: MediaItem, item_data: Dict[str, Any]) -> List[str]:
        """Update an existing MediaItem with new data"""
        try:
            changes = []
            
            # Check if key fields have changed
            new_title = item_data.get('title', 'Unknown Title')
            new_summary = item_data.get('summary') or item_data.get('plot') or item_data.get('overview')
            new_year = item_data.get('year')
            new_rating = item_data.get('rating')
            
            if item.title != new_title:
                changes.append(f"Title: '{item.title}' → '{new_title}'")
                item.title = new_title
                item.sort_title = item_data.get('sort_title') or new_title
            
            if item.summary != new_summary:
                changes.append("Summary updated")
                item.summary = new_summary
            
            if item.year != new_year:
                changes.append(f"Year: {item.year} → {new_year}")
                item.year = new_year
            
            if item.rating != new_rating:
                old_rating = f"{item.rating:.1f}" if item.rating else "None"
                new_rating_str = f"{new_rating:.1f}" if new_rating else "None"
                changes.append(f"Rating: {old_rating} → {new_rating_str}")
                item.rating = new_rating
            
            # Always update last_synced and extra_metadata
            item.last_synced = datetime.utcnow()
            item.extra_metadata = item_data.get('raw_data', {})
            
            if changes:
                db.session.add(item)
            
            return changes
            
        except Exception as e:
            current_app.logger.error(f"Error updating media item {item.external_id}: {e}")
            return []
    
    @staticmethod
    def get_cached_library_content(library_id: int, page: int = 1, per_page: int = 24, 
                                 search_query: str = '', sort_by: str = 'title_asc') -> Dict[str, Any]:
        """
        Get library content from cached database
        
        Args:
            library_id: ID of the library
            page: Page number
            per_page: Items per page
            search_query: Search query string
            sort_by: Sort criteria ('title_asc', 'title_desc', 'year_asc', 'year_desc', 'added_at_asc', 'added_at_desc', 'rating_asc', 'rating_desc', 'total_streams_asc', 'total_streams_desc')
            
        Returns:
            Dict with paginated results
        """
        try:
            # Build query
            query = MediaItem.query.filter_by(library_id=library_id)
            
            # Apply search filter if provided
            if search_query:
                search_term = f"%{search_query.lower()}%"
                query = query.filter(
                    or_(
                        MediaItem.title.ilike(search_term),
                        MediaItem.summary.ilike(search_term)
                    )
                )
            
            # Apply sorting
            if sort_by.startswith('total_streams'):
                # Join with MediaStreamHistory to get stream counts
                from app.models_media_services import MediaStreamHistory
                query = query.outerjoin(
                    MediaStreamHistory,
                    and_(
                        MediaStreamHistory.server_id == MediaItem.server_id,
                        MediaStreamHistory.media_title == MediaItem.title
                    )
                ).group_by(MediaItem.id)
                
                if sort_by == 'total_streams_desc':
                    query = query.order_by(
                        db.func.count(MediaStreamHistory.id).desc(),
                        MediaItem.sort_title.asc()  # Secondary sort by title
                    )
                else:  # total_streams_asc
                    query = query.order_by(
                        db.func.count(MediaStreamHistory.id).asc(),
                        MediaItem.sort_title.asc()  # Secondary sort by title
                    )
            elif sort_by.startswith('year'):
                if sort_by == 'year_desc':
                    query = query.order_by(MediaItem.year.desc().nullslast(), MediaItem.sort_title.asc())
                else:  # year_asc
                    query = query.order_by(MediaItem.year.asc().nullsfirst(), MediaItem.sort_title.asc())
            elif sort_by.startswith('added_at'):
                if sort_by == 'added_at_desc':
                    query = query.order_by(MediaItem.added_at.desc().nullslast(), MediaItem.sort_title.asc())
                else:  # added_at_asc
                    query = query.order_by(MediaItem.added_at.asc().nullsfirst(), MediaItem.sort_title.asc())
            elif sort_by.startswith('rating'):
                if sort_by == 'rating_desc':
                    query = query.order_by(MediaItem.rating.desc().nullslast(), MediaItem.sort_title.asc())
                else:  # rating_asc
                    query = query.order_by(MediaItem.rating.asc().nullsfirst(), MediaItem.sort_title.asc())
            elif sort_by == 'title_desc':
                query = query.order_by(MediaItem.sort_title.desc())
            else:  # Default to title_asc
                query = query.order_by(MediaItem.sort_title.asc())
            
            # Get total count
            total = query.count()
            
            # Apply pagination
            items = query.offset((page - 1) * per_page).limit(per_page).all()
            
            # Convert to dict format and add stream counts
            items_data = []
            for item in items:
                item_dict = item.to_dict()
                
                # Get stream count for this item
                from app.models_media_services import MediaStreamHistory
                stream_count = MediaStreamHistory.query.filter(
                    MediaStreamHistory.server_id == item.server_id,
                    MediaStreamHistory.media_title == item.title
                ).count()
                
                item_dict['stream_count'] = stream_count
                items_data.append(item_dict)
            
            # Calculate pagination info
            total_pages = (total + per_page - 1) // per_page
            
            return {
                'items': items_data,
                'total': total,
                'page': page,
                'per_page': per_page,
                'pages': total_pages,
                'has_prev': page > 1,
                'has_next': page < total_pages
            }
            
        except Exception as e:
            current_app.logger.error(f"Error getting cached library content: {e}")
            return {
                'items': [],
                'total': 0,
                'page': page,
                'per_page': per_page,
                'pages': 0,
                'has_prev': False,
                'has_next': False,
                'error': str(e)
            }
    
    @staticmethod
    def is_library_synced(library_id: int, max_age_hours: int = 24) -> bool:
        """
        Check if library has been synced recently
        
        Args:
            library_id: ID of the library
            max_age_hours: Maximum age of sync in hours
            
        Returns:
            True if library has been synced within max_age_hours
        """
        try:
            cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)
            
            # Check if we have any items synced recently
            recent_item = MediaItem.query.filter(
                and_(
                    MediaItem.library_id == library_id,
                    MediaItem.last_synced >= cutoff_time
                )
            ).first()
            
            return recent_item is not None
            
        except Exception as e:
            current_app.logger.error(f"Error checking library sync status: {e}")
            return False