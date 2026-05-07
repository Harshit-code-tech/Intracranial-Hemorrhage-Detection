/**
 * Profile Actions - Handle inline editing, modals, and avatar upload
 */

// Avatar Upload
document.getElementById('avatarUpload')?.addEventListener('change', async function(e) {
  const file = e.target.files[0];
  if (!file) return;

  const btn = document.querySelector('.avatar-upload-btn');
  const originalHtml = btn.innerHTML;
  btn.innerHTML = '<span style="width:14px;height:14px;border:2px solid transparent;border-top-color:#fff;border-radius:50%;animation:spin 1s linear infinite;display:inline-block;"></span>';
  btn.disabled = true;

  const formData = new FormData();
  formData.append('avatar', file);

  try {
    const response = await fetch('/auth/profile/upload-avatar', {
      method: 'POST',
      body: formData
    });
    
    const data = await response.json();
    if (response.ok) {
      // Reload to show new avatar everywhere
      window.location.reload();
    } else {
      showProfileMessage(data.error || 'Failed to upload avatar', 'error');
      btn.innerHTML = originalHtml;
      btn.disabled = false;
    }
  } catch (err) {
    showProfileMessage('Network error occurred', 'error');
    btn.innerHTML = originalHtml;
    btn.disabled = false;
  }
});

// Modals
function openEditModal(field, currentValue) {
  document.getElementById('editFieldType').value = field;
  document.getElementById('editFieldValue').value = currentValue;
  
  let label = 'New Value';
  let title = 'Edit Field';
  if (field === 'username') { label = 'New Username'; title = 'Change Username'; }
  if (field === 'email') { label = 'New Email Address'; title = 'Change Email'; }
  if (field === 'full_name') { label = 'New Full Name'; title = 'Update Name'; }
  
  document.getElementById('editFieldLabel').innerText = label;
  document.getElementById('editModalTitle').innerText = title;
  
  document.getElementById('editStep1').style.display = 'block';
  document.getElementById('editStep2').style.display = 'none';
  document.getElementById('editFieldOtpToken').value = '';
  document.getElementById('editFieldOtp').value = '';
  
  document.getElementById('editModal').style.display = 'flex';
}

function closeEditModal() {
  document.getElementById('editModal').style.display = 'none';
}

function openDeleteModal() {
  document.getElementById('deleteModal').style.display = 'flex';
}

function closeDeleteModal() {
  document.getElementById('deleteModal').style.display = 'none';
}

// Edit Form Submission
document.getElementById('editFieldForm')?.addEventListener('submit', async function(e) {
  e.preventDefault();
  
  const field = document.getElementById('editFieldType').value;
  const value = document.getElementById('editFieldValue').value.trim();
  const btn = document.getElementById('btnRequestChange');
  
  if (!value) return;
  
  const originalText = btn.innerText;
  btn.innerText = 'Saving...';
  btn.disabled = true;
  
  let endpoint = '';
  let payload = {};
  
  if (field === 'full_name') {
    endpoint = '/auth/profile/update-name';
    payload = { full_name: value };
  } else if (field === 'username') {
    endpoint = '/auth/profile/request-username-change';
    payload = { new_username: value };
  } else if (field === 'email') {
    endpoint = '/auth/profile/request-email-change';
    payload = { new_email: value };
  }
  
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    
    if (res.ok) {
      if (field === 'full_name') {
        document.getElementById('val-full_name').innerText = data.full_name;
        closeEditModal();
        showProfileMessage('Name updated successfully', 'success');
      } else {
        // Show OTP step
        document.getElementById('editStep1').style.display = 'none';
        document.getElementById('editStep2').style.display = 'block';
        document.getElementById('editFieldOtpToken').value = data.otp_token;
      }
    } else {
      showProfileMessage(data.error || 'Failed to request change', 'error');
    }
  } catch (err) {
    showProfileMessage('Network error occurred', 'error');
  } finally {
    btn.innerText = originalText;
    btn.disabled = false;
  }
});

// Confirm OTP (Username/Email)
document.getElementById('btnConfirmChange')?.addEventListener('click', async function() {
  const otp = document.getElementById('editFieldOtp').value.trim();
  const token = document.getElementById('editFieldOtpToken').value;
  const field = document.getElementById('editFieldType').value;
  const btn = this;
  
  if (otp.length !== 6) return;
  
  const originalText = btn.innerText;
  btn.innerText = 'Verifying...';
  btn.disabled = true;
  
  const purpose = field === 'username' ? 'change_username' : 'change_email';
  
  try {
    const res = await fetch('/auth/profile/confirm-change', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ otp: otp, otp_token: token, purpose: purpose })
    });
    const data = await res.json();
    
    if (res.ok) {
      // Reload to reflect changes
      window.location.reload();
    } else {
      showProfileMessage(data.error || 'Failed to verify OTP', 'error');
    }
  } catch (err) {
    showProfileMessage('Network error occurred', 'error');
  } finally {
    btn.innerText = originalText;
    btn.disabled = false;
  }
});

function showProfileMessage(msg, type) {
  const container = document.getElementById('profileMessage');
  if (!container) return;
  container.innerHTML = `<div class="alert alert-${type}">${msg}</div>`;
  setTimeout(() => { container.innerHTML = ''; }, 5000);
}
