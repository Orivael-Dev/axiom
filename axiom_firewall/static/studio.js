/* Axiom SRD Studio — client logic */

'use strict';

// SLOT_LIMIT, LOCKED_SLOTS, TIER, CONTAINER_CAP injected by template

// Track which slot index holds which module type
const slotTypes = {};  // index -> type string

/* ── Drag-and-drop ─────────────────────────────────────────────── */

document.querySelectorAll('.palette-card').forEach(card => {
  const slotType = card.dataset.slot;
  const locked   = card.dataset.locked === 'true';

  if (locked) {
    card.addEventListener('click', () => {
      window.location.href = '/billing';
    });
    return;
  }

  card.setAttribute('draggable', 'true');
  card.addEventListener('dragstart', e => {
    e.dataTransfer.setData('slot_type', slotType);
    e.dataTransfer.effectAllowed = 'copy';
  });
});

function handleDrop(event, index) {
  event.preventDefault();
  const zone = document.getElementById('slot-' + index);
  zone.classList.remove('drag-over');

  const slotType = event.dataTransfer.getData('slot_type');
  if (!slotType) return;

  activateSlot(index, slotType);
}

function activateSlot(index, slotType) {
  if (LOCKED_SLOTS.includes(slotType)) {
    showToast('Upgrade to access ' + slotType + ' slots →');
    return;
  }

  const activeCount = Object.keys(slotTypes).length;
  // +1 for the always-present text slot (slot-0); limit applies to extras
  if (activeCount >= SLOT_LIMIT - 1) {
    document.getElementById('upgrade-banner').hidden = false;
    showToast('Slot limit reached (' + SLOT_LIMIT + ' total). Upgrade for more.');
    return;
  }

  // Deactivate existing content in this zone if any
  if (slotTypes[index]) {
    clearSlotForms(index);
  }

  slotTypes[index] = slotType;
  const zone = document.getElementById('slot-' + index);
  zone.classList.remove('slot-empty');
  zone.removeAttribute('ondragover');
  zone.removeAttribute('ondragleave');
  zone.removeAttribute('ondrop');

  const placeholder = zone.querySelector('.slot-placeholder');
  if (placeholder) placeholder.hidden = true;

  const form = zone.querySelector('.slot-form[data-type="' + slotType + '"]');
  if (form) form.hidden = false;
}

function removeSlot(index) {
  delete slotTypes[index];

  const zone = document.getElementById('slot-' + index);
  clearSlotForms(index);

  zone.classList.add('slot-empty');
  zone.setAttribute('ondragover', "event.preventDefault(); this.classList.add('drag-over')");
  zone.setAttribute('ondragleave', "this.classList.remove('drag-over')");
  zone.setAttribute('ondrop', 'handleDrop(event, ' + index + ')');

  const placeholder = zone.querySelector('.slot-placeholder');
  if (placeholder) placeholder.hidden = false;

  // Hide upgrade banner if we're back under the limit
  const activeCount = Object.keys(slotTypes).length;
  if (activeCount < SLOT_LIMIT - 1) {
    document.getElementById('upgrade-banner').hidden = true;
  }
}

function clearSlotForms(index) {
  const zone = document.getElementById('slot-' + index);
  zone.querySelectorAll('.slot-form').forEach(f => { f.hidden = true; });
}

/* ── Gather config from DOM ─────────────────────────────────────── */

function gatherConfig() {
  const modelId    = document.getElementById('model_id').value.trim();
  const hardware   = document.getElementById('hardware_map').value;
  const quant      = document.getElementById('quant_scheme').value;
  const fmt        = _selectedExportFmt();

  const slots = [];
  for (const [idx, type] of Object.entries(slotTypes)) {
    const params = {};
    const zone   = document.getElementById('slot-' + idx);

    if (type === 'governance') {
      const strict = zone.querySelector('input[name^="gov_strict"]');
      params.strict = strict ? strict.checked : true;
    } else if (type === 'audio') {
      const impact   = zone.querySelector('select[name^="audio_impact"]');
      const material = zone.querySelector('select[name^="audio_material"]');
      params.impact_profile    = impact   ? impact.value   : 'sharp_transient';
      params.material_signature = material ? material.value : 'glass-like';
    } else if (type === 'video') {
      const motion = zone.querySelector('select[name^="video_motion"]');
      const impact = zone.querySelector('input[name^="video_impact"]');
      params.motion_class    = motion ? motion.value : 'downward';
      params.impact_detected = impact ? impact.checked : false;
    } else if (type === 'physics') {
      const mat  = zone.querySelector('select[name^="physics_material"]');
      const surf = zone.querySelector('select[name^="physics_surface"]');
      params.material = mat  ? mat.value  : 'brittle_glass';
      params.surface  = surf ? surf.value : 'hard_surface';
    } else if (type === 'adapter') {
      const atype = zone.querySelector('select[name^="adapter_type"]');
      params.adapter_type = atype ? atype.value : 'security';
    }

    slots.push({ slot_type: type, params });
  }

  return {
    model_id:      modelId,
    slots,
    hardware_map:  hardware,
    export_format: fmt,
    quant_scheme:  quant,
  };
}

let _lastFmt = 'colab';
function _selectedExportFmt() { return _lastFmt; }

/* ── Export ─────────────────────────────────────────────────────── */

async function doExport(fmt) {
  _lastFmt = fmt;
  const cfg = gatherConfig();

  if (!cfg.model_id) {
    showToast('Enter a HuggingFace model ID first.');
    return;
  }

  const ext = {colab: '.ipynb', jupyter: '.ipynb', python: '.py', json: '.json'}[fmt];
  const slug = (cfg.model_id.split('/').pop() || 'model').toLowerCase().replace(/[^a-z0-9]/g, '-');
  const filename = 'axiom_' + slug + '_' + fmt + ext;

  try {
    const res = await fetch('/dashboard/studio/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });

    if (!res.ok) {
      const err = await res.text();
      showToast('Export failed: ' + err.slice(0, 120));
      return;
    }

    const blob = await res.blob();
    triggerDownload(blob, filename);
  } catch (e) {
    showToast('Export error: ' + e.message);
  }
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a   = document.createElement('a');
  a.href     = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/* ── Verify .axm upload ─────────────────────────────────────────── */

const axmInput   = document.getElementById('axm-upload');
const fpBadge    = document.getElementById('fp-badge');
const fpSpinner  = document.getElementById('fp-spinner');
const fpDetail   = document.getElementById('fp-detail');

axmInput.addEventListener('change', async () => {
  const file = axmInput.files[0];
  if (!file) return;

  if (file.size > 64 * 1024 * 1024) {
    fpBadge.textContent = 'File too large (max 64 MB)';
    fpBadge.className   = 'fp-badge fp-failed';
    showToast('AXM file too large. Verify locally with axm_cli.py verify');
    return;
  }

  fpBadge.textContent = 'Verifying...';
  fpBadge.className   = 'fp-badge fp-unverified';
  fpSpinner.hidden    = false;
  fpDetail.hidden     = true;

  const fd = new FormData();
  fd.append('axm', file);

  try {
    const res  = await fetch('/dashboard/studio/verify', { method: 'POST', body: fd });
    const data = await res.json();

    if (data.verified) {
      const fp = data.fingerprint || '';
      fpBadge.textContent = '✓ ' + fp.slice(0, 16) + '…';
      fpBadge.className   = 'fp-badge fp-verified';
      fpDetail.textContent = 'fingerprint: ' + fp + '\nproofs: ' + (data.proofs_checked || '?');
      fpDetail.hidden      = false;
    } else {
      fpBadge.textContent = '✗ Tamper detected';
      fpBadge.className   = 'fp-badge fp-failed';
      if (data.error) {
        fpDetail.textContent = data.error.slice(0, 300);
        fpDetail.hidden      = false;
      }
    }
  } catch (e) {
    fpBadge.textContent = '✗ Verify error';
    fpBadge.className   = 'fp-badge fp-failed';
    fpDetail.textContent = e.message;
    fpDetail.hidden      = false;
  } finally {
    fpSpinner.hidden = true;
    axmInput.value   = '';
  }
});

/* ── Save container config ──────────────────────────────────────── */

const saveBtn    = document.getElementById('save-btn');
const saveResult = document.getElementById('save-result');

async function saveContainer() {
  const cfg = gatherConfig();
  if (!cfg.model_id) {
    showToast('Enter a model ID before saving.');
    return;
  }

  saveBtn.disabled = true;
  saveResult.hidden = true;

  try {
    const res  = await fetch('/dashboard/studio/containers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    });
    const data = await res.json();

    if (res.ok) {
      saveResult.textContent = '✓ Saved: ' + (data.name || cfg.model_id);
      saveResult.className   = 'save-result ok';
      saveResult.hidden      = false;
      // Update cap note
      if (CONTAINER_CAP !== null && data.count !== undefined) {
        const note = document.getElementById('container-cap-note');
        if (note) note.textContent = 'Free: ' + data.count + '/' + CONTAINER_CAP + ' saved configs used.';
        if (data.count >= CONTAINER_CAP) saveBtn.disabled = true;
      }
    } else {
      saveResult.textContent = data.detail || 'Save failed';
      saveResult.className   = 'save-result err';
      saveResult.hidden      = false;
    }
  } catch (e) {
    saveResult.textContent = 'Save error: ' + e.message;
    saveResult.className   = 'save-result err';
    saveResult.hidden      = false;
  } finally {
    saveBtn.disabled = false;
  }
}

/* ── Toast helper ───────────────────────────────────────────────── */

let _toastTimer = null;

function showToast(msg) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.hidden = false;
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { toast.hidden = true; }, 3500);
}
