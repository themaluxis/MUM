// App User Management JavaScript Functions

/**
 * Navigate to app user profile/details
 * @param {string} username - The username of the app user
 */
function viewAppUser(username) {
    console.log('Viewing app user:', username);
    // Navigate to the username-based profile page
    window.location.href = `/user/${encodeURIComponent(username)}`;
}

/**
 * Navigate to service user profile/details
 * @param {number} serviceUserId - The ID of the service user
 */
function viewServiceAccount(serviceUserId) {
    console.log('Viewing service account:', serviceUserId);
    // For now, we'll show an alert - later this can navigate to a profile page
    showToast(`Viewing service account (ID: ${serviceUserId})`, 'info');
    
    // TODO: Implement actual navigation
    // window.location.href = `/users/service/${serviceUserId}`;
}

/**
 * Open edit modal for local user
 * @param {number} localUserId - The ID of the local user to edit
 */
function editAppUser(appUserId) {
    console.log('Editing app user:', appUserId);
    
    // Show loading state
    showToast('Loading local user edit form...', 'info');
    
    // Make HTMX request to get edit form
    htmx.ajax('GET', `/users/local/${localUserId}/edit`, {
        target: '#localUserEditModalContainer',
        swap: 'innerHTML'
    }).then(() => {
        // Open the modal after content is loaded
        const modal = document.getElementById('localUserEditModal');
        if (modal) {
            modal.showModal();
        }
    }).catch((error) => {
        console.error('Error loading local user edit form:', error);
        showToast('Error loading edit form', 'error');
    });
}

/**
 * Show all linked accounts for a local user
 * @param {number} localUserId - The ID of the local user
 */
function viewLinkedAccounts(localUserId) {
    console.log('Viewing linked accounts for local user:', localUserId);
    
    // Make HTMX request to get linked accounts view
    htmx.ajax('GET', `/users/local/${localUserId}/linked-accounts`, {
        target: '#linkedAccountsModalContainer',
        swap: 'innerHTML'
    }).then(() => {
        // Open the modal after content is loaded
        const modal = document.getElementById('linkedAccountsModal');
        if (modal) {
            modal.showModal();
        }
    }).catch((error) => {
        console.error('Error loading linked accounts:', error);
        showToast('Error loading linked accounts', 'error');
    });
}

/**
 * Link a service account to a local user
 * @param {number} localUserId - The ID of the local user
 * @param {number} serviceUserId - The ID of the service user to link
 */
function linkServiceAccount(localUserId, serviceUserId) {
    console.log('Linking service account:', serviceUserId, 'to local user:', localUserId);
    
    if (!confirm('Are you sure you want to link these accounts?')) {
        return;
    }
    
    // Show loading state
    showToast('Linking accounts...', 'info');
    
    // Make HTMX request to link accounts
    htmx.ajax('POST', `/users/local/${localUserId}/link/${serviceUserId}`, {
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        }
    }).then(() => {
        showToast('Accounts linked successfully', 'success');
        // Refresh the user list to show updated linking
        refreshUserList();
    }).catch((error) => {
        console.error('Error linking accounts:', error);
        showToast('Error linking accounts', 'error');
    });
}

/**
 * Unlink a service account from its local user
 * @param {number} serviceUserId - The ID of the service user to unlink
 */
function unlinkServiceAccount(serviceUserId) {
    console.log('Unlinking service account:', serviceUserId);
    
    if (!confirm('Are you sure you want to unlink this service account? This will remove the connection to the local user.')) {
        return;
    }
    
    // Show loading state
    showToast('Unlinking account...', 'info');
    
    // Make HTMX request to unlink account
    htmx.ajax('POST', `/users/service/${serviceUserId}/unlink`, {
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        }
    }).then(() => {
        showToast('Account unlinked successfully', 'success');
        // Refresh the user list to show updated linking
        refreshUserList();
    }).catch((error) => {
        console.error('Error unlinking account:', error);
        showToast('Error unlinking account', 'error');
    });
}

/**
 * Filter users by type (all, local, service)
 * @param {string} userType - The type of users to show
 */
function filterUsersByType(userType) {
    console.log('Filtering users by type:', userType);
    
    // Update the URL parameter and reload the list
    const url = new URL(window.location);
    url.searchParams.set('user_type', userType);
    url.searchParams.set('page', '1'); // Reset to first page
    
    // Use HTMX to load the filtered content
    htmx.ajax('GET', url.toString(), {
        target: '#user-list-content',
        swap: 'outerHTML'
    });
    
    // Update the URL without page reload
    window.history.pushState({}, '', url.toString());
}

/**
 * Refresh the user list (used after operations that modify user data)
 */
function refreshUserList() {
    console.log('Refreshing user list...');
    
    // Use HTMX to reload the current user list
    htmx.ajax('GET', window.location.href, {
        target: '#user-list-content',
        swap: 'outerHTML'
    });
}

/**
 * Show a toast notification
 * @param {string} message - The message to show
 * @param {string} category - The category (success, error, info, warning)
 */
function showToast(message, category = 'info') {
    // Trigger the existing toast system
    const toastEvent = new CustomEvent('showToastEvent', {
        detail: { message, category }
    });
    document.dispatchEvent(toastEvent);
}

// Initialize event listeners when the DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    console.log('Local users JavaScript initialized');
    
    // Add event listener for user type filter dropdown
    const userTypeFilter = document.getElementById('userTypeFilter');
    if (userTypeFilter) {
        userTypeFilter.addEventListener('change', function() {
            filterUsersByType(this.value);
        });
    }
});

// Export functions for global access
window.viewAppUser = viewAppUser;
window.viewServiceAccount = viewServiceAccount;
window.editAppUser = editAppUser;
window.viewLinkedAccounts = viewLinkedAccounts;
window.linkServiceAccount = linkServiceAccount;
window.unlinkServiceAccount = unlinkServiceAccount;
window.filterUsersByType = filterUsersByType;
window.refreshUserList = refreshUserList;