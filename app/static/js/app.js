// File: app/static/js/app.js

document.addEventListener('DOMContentLoaded', function () {
    // --- Theme toggler ---
    const themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
        const storedTheme = localStorage.getItem('theme');
        const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        let currentTheme = storedTheme || (systemPrefersDark ? 'dark' : 'light');
        
        document.documentElement.setAttribute('data-theme', currentTheme);
        themeToggle.checked = currentTheme === 'dark';

        themeToggle.addEventListener('change', function () {
            const newTheme = this.checked ? 'dark' : 'light';
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
        });
    }

    // --- Mobile sidebar toggle (DaisyUI drawer is mainly CSS driven) ---
    // Add specific JS here if needed for custom sidebar interactions beyond DaisyUI's checkbox.

    // --- Auto-dismiss standard Flask flash messages ---
    function initializeFlashMessageDismissal(containerElement) {
        const searchRoot = containerElement || document.body;
        searchRoot.querySelectorAll('.flash-alert-auto-dismiss').forEach(function(message) {
            if (!message.getAttribute('data-dismiss-timer-active')) {
                message.setAttribute('data-dismiss-timer-active', 'true');
                setTimeout(function() {
                    message.style.transition = 'opacity 0.5s ease';
                    message.style.opacity = '0';
                    setTimeout(() => message.remove(), 500); 
                }, 5000); 
            }
        });
    }
    // Initial call for messages present on full page load (standard flash container)
    const standardFlashContainer = document.getElementById('standard-flash-messages');
    if (standardFlashContainer) {
        initializeFlashMessageDismissal(standardFlashContainer);
    }
    // OOB flash container (if you use OOB for other things)
    const oobFlashContainer = document.getElementById('htmx-oob-flash-messages');
    if (oobFlashContainer) {
        initializeFlashMessageDismissal(oobFlashContainer);
    }

    // --- Client-side Toast Notification System ---
    // Ensure a <div id="toast-container" class="toast ..."></div> exists in your base.html
    function showToast(message, type = 'info', duration = 5000) {
        const toastContainer = document.getElementById('toast-container');
        if (!toastContainer) {
            console.warn('JS app.js - showToast(): Toast container (#toast-container) not found!');
            return;
        }
        let alertClass = 'alert-info'; // Default DaisyUI alert type
        if (type === 'success') alertClass = 'alert-success';
        else if (type === 'error' || type === 'danger') alertClass = 'alert-error'; // Map 'danger' from Flask to 'error'
        else if (type === 'warning') alertClass = 'alert-warning';

        const toastId = 'toast-' + Date.now() + Math.random().toString(36).substr(2, 5);
        const toastElement = document.createElement('div');
        toastElement.id = toastId;
        toastElement.className = `alert ${alertClass} shadow-lg w-auto relative overflow-hidden block`; 
        toastElement.style.opacity = '0';
        
        // Create toast content with progress bar and close button
        toastElement.innerHTML = `
            <div class="flex items-center justify-between w-full">
                <span>${message}</span>
                <button class="btn btn-ghost btn-xs ml-2" onclick="this.parentElement.parentElement.remove()">
                    <i class="fa-solid fa-times"></i>
                </button>
            </div>
            <div class="absolute bottom-0 left-0 bg-black bg-opacity-20 transition-all ease-linear toast-progress h-full opacity-[.15] pointer-events-none" style="width: 100%;"></div>
        `;
        
        toastContainer.appendChild(toastElement);

        // Fade in the toast
        setTimeout(() => { 
            toastElement.style.transition = 'opacity 0.3s ease-in-out'; 
            toastElement.style.opacity = '1'; 
        }, 10);

        // Progress bar and auto-dismiss logic
        const progressBar = toastElement.querySelector('.toast-progress');
        let startTime = Date.now();
        let isPaused = false;
        let remainingTime = duration;
        let animationId;

        function updateProgress() {
            if (isPaused) return;
            
            const elapsed = Date.now() - startTime;
            const progress = Math.max(0, (remainingTime - elapsed) / duration * 100);
            
            if (progressBar) {
                progressBar.style.width = progress + '%';
            }
            
            if (elapsed >= remainingTime) {
                removeToast();
            } else {
                animationId = requestAnimationFrame(updateProgress);
            }
        }

        function removeToast() {
            if (animationId) {
                cancelAnimationFrame(animationId);
            }
            const toastToRemove = document.getElementById(toastId);
            if (toastToRemove) {
                toastToRemove.style.opacity = '0';
                setTimeout(() => toastToRemove.remove(), 300);
            }
        }

        // Hover functionality - pause on hover and reset timer, resume on leave
        toastElement.addEventListener('mouseenter', () => {
            isPaused = true;
            if (animationId) {
                cancelAnimationFrame(animationId);
            }
            // Reset the timer completely on hover
            remainingTime = duration;
            if (progressBar) {
                progressBar.style.width = '100%';
            }
        });

        toastElement.addEventListener('mouseleave', () => {
            isPaused = false;
            startTime = Date.now(); // Reset start time
            remainingTime = duration; // Ensure we start with full duration
            updateProgress();
        });

        // Start the progress animation
        updateProgress();
    }
    // Make showToast globally accessible so HTMX event listener can call it.
    window.showToast = showToast;

    // --- HTMX Event Listeners ---

    // Listener for custom event from HX-Trigger (e.g., 'showToastEvent')
    // Ensure the event name here matches exactly what you send in the HX-Trigger header's JSON key.
    document.body.addEventListener('showToastEvent', function(evt) {
        console.debug("JS app.js: Received 'showToastEvent' from HX-Trigger. Detail:", evt.detail);
        if (evt.detail && typeof evt.detail.message !== 'undefined') { // Check for message property
            let messageText = evt.detail.message;
            let messageCategory = evt.detail.category || 'info'; // Default to 'info'
            
            // Call the globally available showToast function
            if (typeof window.showToast === 'function') {
                window.showToast(messageText, messageCategory);
            } else {
                console.error("JS app.js: showToast function is not defined globally.");
            }
        } else {
            console.warn("JS app.js: 'showToastEvent' received, but evt.detail.message is missing or undefined.", evt.detail);
        }
    });

    // General HTMX configuration request listener (e.g., for CSRF)
    document.body.addEventListener('htmx:configRequest', function(evt) {
        const csrfTokenMeta = document.querySelector('meta[name="csrf-token"]');
        if (csrfTokenMeta) {
            // Add CSRF token to POST, PUT, DELETE, PATCH requests (methods that can modify state)
            if (evt.detail.verb && typeof evt.detail.verb === 'string') {
                const method = evt.detail.verb.toLowerCase();
                if (method !== 'get' && method !== 'head' && method !== 'options') {
                     evt.detail.headers['X-CSRFToken'] = csrfTokenMeta.getAttribute('content');
                }
            }
        }
    });

    // Handle validation errors (HTTP 422) gracefully for HTMX form submissions
    document.body.addEventListener('htmx:beforeSwap', function(evt) {
        if (evt.detail.xhr.status === 422) { 
            evt.detail.shouldSwap = true; 
            evt.detail.isError = false; 
        }
    });
    
    // After HTMX swaps content, re-initialize components if necessary
    document.body.addEventListener('htmx:afterSwap', function(event) {
        console.debug("JS app.js - htmx:afterSwap triggered. Target ID:", event.detail.target.id, "Swapped element:", event.detail.elt);
        
        // Re-initialize standard flash message dismissal if OOB swaps add them to #htmx-oob-flash-messages
        const oobFlashContainerAfterSwap = document.getElementById('htmx-oob-flash-messages');
        if (oobFlashContainerAfterSwap) {
            initializeFlashMessageDismissal(oobFlashContainerAfterSwap);
        }
        // Also check the direct target of the swap
        if (event.detail.target) {
            initializeFlashMessageDismissal(event.detail.target);
        }

        // Call page-specific re-initialization functions if they are defined globally
        // These functions would typically be defined in the script block of their respective main templates (e.g., list.html)
        // and attached to the window object (e.g., window.reinitializeUserListFeatures = function() {...})
        if (event.detail.target.id === 'user-list-container' && typeof window.reinitializeUserListFeatures === 'function') {
            console.debug("JS app.js - htmx:afterSwap: Calling window.reinitializeUserListFeatures() for #user-list-container.");
            window.reinitializeUserListFeatures();
        }
        if (event.detail.target.id === 'invites-list-table-container' && typeof window.reinitializeInviteListFeatures === 'function') {
            console.debug("JS app.js - htmx:afterSwap: Calling window.reinitializeInviteListFeatures().");
            window.reinitializeInviteListFeatures();
        }
        if (event.detail.target.id === 'history_table_container' && typeof window.reinitializeHistoryListFeatures === 'function') {
            console.debug("JS app.js - htmx:afterSwap: Calling window.reinitializeHistoryListFeatures().");
            window.reinitializeHistoryListFeatures();
        }
        // Add more for other HTMX-updated containers as needed
    });

    // Handle HX-Redirect response header for full page redirects from HTMX responses
    document.body.addEventListener('htmx:responseHeader', function(event) {
        if (event.detail.xhr.getResponseHeader('HX-Redirect')) {
            window.location.href = event.detail.xhr.getResponseHeader('HX-Redirect');
        }
    });

    // --- Session Count Badge Monitoring ---
    function updateSessionBadges() {
        // Check if navbar stream badge is enabled first
        fetch('/api/settings/navbar-stream-badge-status')
            .then(response => response.json())
            .then(statusData => {
                const desktopBadge = document.getElementById('streaming-badge-desktop');
                const mobileBadge = document.getElementById('streaming-badge-mobile');
                
                if (!desktopBadge && !mobileBadge) {
                    return; // No badges found, user might not be logged in
                }

                if (!statusData.enabled) {
                    // Hide badges when feature is disabled
                    if (desktopBadge) desktopBadge.style.display = 'none';
                    if (mobileBadge) mobileBadge.style.display = 'none';
                    console.debug('FRONTEND: Navbar stream badge disabled - hiding badges');
                    return;
                }

                console.debug('FRONTEND: Requesting session count (navbar badge enabled)');

                // Fetch session count
                fetch('/api/streaming/sessions/count', {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
            },
            credentials: 'same-origin'
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const count = data.count;
                
                // Log server-side throttling info
                if (data.cached) {
                    console.debug(`FRONTEND: Using cached session data (${data.time_since_last_check}s old)`);
                } else {
                    console.debug(`FRONTEND: Fresh session data fetched`);
                }
                
                // Update both desktop and mobile badges
                [desktopBadge, mobileBadge].forEach(badge => {
                    if (badge) {
                        badge.textContent = count;
                        
                        // Show/hide badge with smooth transition
                        if (count > 0) {
                            if (badge.style.display === 'none') {
                                badge.style.display = 'inline-block';
                                badge.style.opacity = '0';
                                badge.style.transform = 'scale(0.8)';
                                badge.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
                                
                                // Trigger animation
                                setTimeout(() => {
                                    badge.style.opacity = '1';
                                    badge.style.transform = 'scale(1)';
                                }, 10);
                            }
                        } else {
                            if (badge.style.display !== 'none') {
                                badge.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
                                badge.style.opacity = '0';
                                badge.style.transform = 'scale(0.8)';
                                
                                setTimeout(() => {
                                    badge.style.display = 'none';
                                }, 300);
                            }
                        }
                    }
                });
            }
        })
                .catch(error => {
                    console.debug('Session count update failed:', error);
                    // Silently fail - don't show errors for this background task
                });
            })
            .catch(error => {
                console.debug('Error checking navbar badge status:', error);
            });
    }

    // Initial session count update
    updateSessionBadges();

    // Set up periodic updates - check if navbar stream badge is enabled
    console.log('FRONTEND: Setting up session monitoring...');
    
    // Check if navbar stream badge is enabled
    fetch('/api/settings/navbar-stream-badge-status')
        .then(response => response.json())
        .then(data => {
            if (data.enabled) {
                console.log('FRONTEND: Navbar stream badge enabled - using 5s updates');
                setInterval(updateSessionBadges, 5000); // 5 seconds for responsive navbar
            } else {
                console.log('FRONTEND: Navbar stream badge disabled - using configured interval');
                // Get the configured session monitoring interval
                fetch('/api/settings/session-monitoring-interval')
                    .then(response => response.json())
                    .then(intervalData => {
                        const intervalSeconds = intervalData.interval || 30;
                        const intervalMs = intervalSeconds * 1000;
                        console.log(`FRONTEND: Setting timer interval to ${intervalSeconds} seconds`);
                        setInterval(updateSessionBadges, intervalMs);
                    })
                    .catch(error => {
                        console.error('FRONTEND: Failed to get interval, using 30s fallback:', error);
                        setInterval(updateSessionBadges, 30000);
                    });
            }
        })
        .catch(error => {
            console.error('FRONTEND: Failed to check navbar badge status, using 30s fallback:', error);
            setInterval(updateSessionBadges, 30000);
        });

    // Update session count when returning to the page (visibility change) - respects throttling
    document.addEventListener('visibilitychange', function() {
        if (!document.hidden) {
            updateSessionBadges(); // Will be throttled automatically
        }
    });

    // Update session count when streaming page content changes (if on streaming page)
    document.body.addEventListener('htmx:afterSwap', function(event) {
        // If we're on the streaming page and content was updated, refresh the badge
        if (event.detail.target && event.detail.target.id === 'streaming-sessions-container') {
            setTimeout(updateSessionBadges, 500); // Small delay to ensure backend is updated
        }
    });

}); // End of DOMContentLoaded

