// Gemini Copilot UI Controller
const { invoke } = window.__TAURI__.core;

// DOM Elements
let queryInput, mainPill, suggestionChips, attachedImage, attachedImageImg;
let processingIndicator, resultsContainer, resultsContent, activitiesContainer;
let micBtn, submitBtn, screenshotBtn, addBtn, removeImageBtn, actionButtons;
let setupWizard;

// State
let isProcessing = false, isRecording = false, hasAttachedImage = false;

// Initialize
window.addEventListener("DOMContentLoaded", () => {
  initElements();
  initEventListeners();
  
  // Run dependency check first — show wizard or proceed normally
  runSetupCheck();

  // Listen for Tauri events
  if (window.__TAURI__) {
    const { listen } = window.__TAURI__.event;
    listen('window-shown', handleWindowShown);
    
    // Listen for real-time activity (thoughts, tools, files)
    listen('gemini-activity', (event) => {
      addActivityBubble(event.payload.kind, event.payload.text);
    });

    // Listen for install progress from backend
    listen('install-progress', (event) => {
      const log = document.getElementById('setup-log');
      if (log) {
        log.classList.remove('hidden');
        log.textContent += event.payload + '\n';
        log.scrollTop = log.scrollHeight;
      }
    });
  }
});

// ============================================
// SETUP WIZARD LOGIC
// ============================================
let isCheckingSetup = false;
let hasPassedSetup = false;

async function runSetupCheck() {
  if (isCheckingSetup || hasPassedSetup) return;
  isCheckingSetup = true;

  try {
    const status = await invoke('check_dependencies');
    
    if (!status.node_installed) {
      setSetupState("Installing Node.js... (Check Terminal)");
      await invoke('install_node');
      
      // Node.js path propagation requires an app restart. Block the user here.
      setTimeout(() => {
        setSetupState("Restart Required! Please exit and reopen Gemini Copilot.");
      }, 5000);
      
      isCheckingSetup = false;
      return;
    }

    if (!status.gemini_installed) {
      setSetupState("Installing Gemini CLI...");
      await invoke('install_gemini_cli');
      // Re-run the setup check after installing
      isCheckingSetup = false;
      setTimeout(runSetupCheck, 1000);
      return;
    }

    if (!status.gemini_logged_in) {
      setSetupState("Awaiting Auth... (Check Browser)");
      await invoke('login_gemini_cli');
      
      const authCheckInterval = setInterval(async () => {
        const authStatus = await invoke('check_dependencies');
        if (authStatus.gemini_logged_in) {
          clearInterval(authCheckInterval);
          setSetupState("Identity Verified! Finalizing...");
          
          // Delay briefly so the user sees the success message before the app disappears
          setTimeout(async () => {
            await invoke('restart_app');
          }, 2000);
          
          hasPassedSetup = true;
          isCheckingSetup = false;
        }
      }, 2000); // Polling every 2s is safe now with the cheap file check
      return;
    }

    // All Good!
    clearSetupState();
    hasPassedSetup = true;
    isCheckingSetup = false;

  } catch (e) {
    console.error('Setup check failed:', e);
    isCheckingSetup = false;
  }
}

function setSetupState(message) {
  isProcessing = true; // Block standard queries during setup
  queryInput.disabled = true;
  queryInput.placeholder = message;
  
  if (processingIndicator) {
    processingIndicator.querySelector('span').textContent = message;
    processingIndicator.classList.remove('hidden');
  }
  if (actionButtons) actionButtons.classList.add('hidden');
  hideSuggestionChips();
}

function clearSetupState() {
  isProcessing = false;
  queryInput.disabled = false;
  queryInput.placeholder = 'Ask Gemini';
  
  if (processingIndicator) {
    processingIndicator.querySelector('span').textContent = 'Thinking...';
    processingIndicator.classList.add('hidden');
  }
  if (actionButtons) actionButtons.classList.remove('hidden');
  
  if (!queryInput.value.trim() && !resultsContainer.classList.contains('visible') && !hasAttachedImage) {
    showSuggestionChips();
  }
  
  queryInput.focus();
}

function initElements() {
  queryInput = document.getElementById('query-input');
  mainPill = document.getElementById('main-pill');
  suggestionChips = document.getElementById('suggestion-chips');
  processingIndicator = document.getElementById('processing-indicator');
  resultsContainer = document.getElementById('results-container');
  resultsContent = document.getElementById('results-content');
  activitiesContainer = document.getElementById('activities-container');
  submitBtn = document.getElementById('submit-btn');
  screenshotBtn = document.getElementById('screenshot-btn');
  attachedImage = document.getElementById('attached-image');
  attachedImageImg = attachedImage.querySelector('img');
  removeImageBtn = attachedImage.querySelector('.remove-image');
  actionButtons = document.querySelector('.action-buttons');
  setupWizard = document.getElementById('setup-wizard');
}

async function handleWindowShown() {
  if (queryInput) queryInput.focus();
  
  if (!isProcessing) {
    try {
      await invoke('reset_session');
      activitiesContainer.innerHTML = '';
      resultsContainer.classList.remove('visible');
      resultsContent.innerHTML = '';
      showSuggestionChips();
      runSetupCheck();
    } catch (e) { console.error('Failed to reset session:', e); }
  }
}

function initEventListeners() {
  queryInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit(); }
  });
  submitBtn.addEventListener('click', handleSubmit);
  
  if (screenshotBtn) {
    screenshotBtn.addEventListener('click', takeScreenshot);
  }
  
  removeImageBtn.addEventListener('click', removeAttachedImage);
  
  document.querySelectorAll('.chip').forEach(chip => {
    chip.addEventListener('click', () => handleChipAction(chip.dataset.action));
  });
  
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') isRecording ? toggleRecording() : hideWindow();
  });

  queryInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      e.stopPropagation();
      handleSubmit();
    }
  });

  queryInput.addEventListener('input', () => {
    // Only show chips if input is empty AND we don't have a visible result
    if (!queryInput.value.trim() && !resultsContainer.classList.contains('visible') && !isProcessing) {
      showSuggestionChips();
    } else {
      hideSuggestionChips();
    }
  });
}

function addActivityBubble(kind, text) {
  // Tool bubbles: replace previous one to keep things clean
  if (kind === 'tool') {
    const existingTool = activitiesContainer.querySelector('.activity-bubble.tool');
    if (existingTool) activitiesContainer.removeChild(existingTool);
  }

  const bubble = document.createElement('div');
  bubble.className = `activity-bubble ${kind}`;
  
  let icon = `
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5">
      <path d="M12 2a10 10 0 1010 10A10 10 0 0012 2zm0 14v.01M12 8v4" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
    
  if (kind === 'tool') {
    icon = `
      <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5">
        <path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.77 3.77z" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>`;
  }
  
  bubble.innerHTML = `<span>${icon}</span> <span>${text}</span>`;
  activitiesContainer.appendChild(bubble);
  
  // Apply inverted triangle widths: newest bubble (bottom) is narrowest,
  // older bubbles (top) get progressively wider
  const bubbles = Array.from(activitiesContainer.querySelectorAll('.activity-bubble'));
  const total = bubbles.length;
  const maxWidth = 420;
  const minWidth = 200;
  bubbles.forEach((b, i) => {
    // i=0 is oldest (top, widest); i=total-1 is newest (bottom, narrowest)
    const fraction = total === 1 ? 0.6 : i / (total - 1);
    const width = Math.round(maxWidth - (maxWidth - minWidth) * fraction);
    b.style.width = `${width}px`;
    b.style.maxWidth = `${width}px`;
    b.style.alignSelf = 'center';
  });

  // Limit stacked bubbles to 6
  while (activitiesContainer.children.length > 6) {
    activitiesContainer.removeChild(activitiesContainer.firstChild);
    // Re-apply triangle after removal
    const remaining = Array.from(activitiesContainer.querySelectorAll('.activity-bubble'));
    remaining.forEach((b, i) => {
      const t = remaining.length;
      const frac = t === 1 ? 0.6 : i / (t - 1);
      const w = Math.round(maxWidth - (maxWidth - minWidth) * frac);
      b.style.width = `${w}px`;
      b.style.maxWidth = `${w}px`;
    });
  }
}

async function handleSubmit() {
  const query = queryInput.value.trim();
  if ((!query && !hasAttachedImage) || isProcessing) return;
  
  // Hide previous responses immediately to drop the "Thinking" pill to the bottom
  resultsContainer.classList.remove('visible');
  resultsContent.innerHTML = '';
  
  setProcessingState(true);
  activitiesContainer.innerHTML = ''; // Clear previous thoughts
  
  try {
    const imageData = hasAttachedImage ? attachedImageImg.src.split(',')[1] : null;
    const result = await invoke('query_gemini', { prompt: query, image: imageData });
    
    if (result.success && result.response) {
      showFinalResponse(result.response);
    } else if (result.success) {
      showFinalResponse("No response received.");
    } else {
      showFinalResponse(result.response || `Query failed: ${result.message}`);
    }
  } catch (error) {
    showFinalResponse(`Error: ${error}`);
  } finally {
    setProcessingState(false);
    clearInput();
  }
}

function showFinalResponse(text) {
  // Clear activity bubbles when the response arrives so they don't overlap
  activitiesContainer.innerHTML = '';
  resultsContainer.classList.add('visible');
  resultsContent.innerHTML = formatResponse(text);
  resultsContent.scrollTop = resultsContent.scrollHeight;
  hideSuggestionChips(); // Ensure chips stay hidden during results
}

function setProcessingState(processing) {
  isProcessing = processing;
  if (processing) {
    processingIndicator.classList.remove('hidden');
    resultsContainer.classList.remove('visible');
    hideSuggestionChips();
    if (actionButtons) actionButtons.classList.add('hidden');
    queryInput.disabled = true;
    queryInput.placeholder = 'Thinking...';
  } else {
    processingIndicator.classList.add('hidden');
    if (actionButtons) actionButtons.classList.remove('hidden');
    queryInput.disabled = false;
    queryInput.placeholder = 'Ask Gemini';
    queryInput.focus();
  }
}

async function takeScreenshot() {
  try {
    const result = await invoke('take_screenshot');
    if (result.success && result.image) {
      showAttachedImage(`data:image/png;base64,${result.image}`);
    }
  } catch (e) { console.error(e); }
}

function showAttachedImage(src) {
  attachedImageImg.src = src;
  attachedImage.classList.remove('hidden');
  hasAttachedImage = true;
}

function removeAttachedImage() {
  attachedImage.classList.add('hidden');
  hasAttachedImage = false;
  // Re-show chips if we are now clean
  if (!queryInput.value.trim() && !resultsContainer.classList.contains('visible')) {
    showSuggestionChips();
  }
}

function clearInput() {
  queryInput.value = '';
  queryInput.style.height = 'auto';
  removeAttachedImage();
}

function showSuggestionChips() { suggestionChips.classList.add('visible'); }
function hideSuggestionChips() { suggestionChips.classList.remove('visible'); }

async function handleChipAction(action) {
  await takeScreenshot();
  const prompts = { summarize: 'Summarize screen', extract: 'Extract text', explain: 'Explain this' };
  queryInput.value = prompts[action] || '';
  handleSubmit();
}

async function hideWindow() {
  await invoke('hide_window');
}

function formatResponse(text) {
  if (!text) return '';
  
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`(.+?)`/g, '<code>$1</code>')
    .split('\n')
    .map(line => {
      const trimmed = line.trim();
      if (trimmed.startsWith('### ')) {
        return `<h3 class="result-heading">${trimmed.substring(4)}</h3>`;
      }
      if (trimmed.startsWith('* ') || trimmed.startsWith('- ') || /^\d+\. /.test(trimmed)) {
        return `<div class="list-item">• ${trimmed.replace(/^(\* |- |\d+\. )/, '')}</div>`;
      }
      return line.length > 0 ? `<p>${line}</p>` : '';
    })
    .join('');
}

function toggleRecording() {
  // TODO: Implement voice input
  isRecording = !isRecording;
}
