import { invoke } from '@tauri-apps/api/core';
import { navigate } from './main';
import type { AppSettings } from './types';
import { escHtml } from './utils';

export async function renderSettingsView(root: HTMLElement): Promise<void> {
  const settings: AppSettings = await invoke('get_settings');

  root.innerHTML = `
    <div class="settings-view">
      <div class="settings-header">
        <h2>Settings</h2>
        <button id="btn-back" class="btn btn-icon" title="Back">&#8592;</button>
      </div>
      <form id="settings-form" class="settings-form" autocomplete="off">
        <div class="form-group">
          <label for="api-key">ElevenLabs API Key</label>
          <input type="password" id="api-key" name="api-key" class="input-full"
            placeholder="sk_…" value="${escHtml(settings.elevenlabsApiKey)}" autocomplete="new-password">
          <small>Stored in the app's config directory and sent only to ElevenLabs.
          Everything else — the model, the voices, where audio goes — comes from the
          audiobook folder AuthorLM writes.</small>
        </div>
        <div class="form-actions">
          <button type="submit" class="btn btn-primary">Save</button>
          <button type="button" id="btn-cancel" class="btn btn-secondary">Cancel</button>
        </div>
      </form>
    </div>`;

  root.querySelector('#btn-back')!.addEventListener('click', () => navigate('book'));
  root.querySelector('#btn-cancel')!.addEventListener('click', () => navigate('book'));
  root.querySelector('#settings-form')!.addEventListener('submit', async (e) => {
    e.preventDefault();
    const apiKey = (root.querySelector('#api-key') as HTMLInputElement).value.trim();
    try {
      await invoke('save_settings', { elevenlabsApiKey: apiKey });
      navigate('book');
    } catch (err) {
      alert(`Failed to save settings: ${err}`);
    }
  });
}
