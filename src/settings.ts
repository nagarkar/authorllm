import { invoke } from '@tauri-apps/api/core';
import { open } from '@tauri-apps/plugin-dialog';
import { navigate } from './main';
import type { AppSettings } from './types';
import { escHtml } from './utils';

const TTS_MODELS = [
  { value: 'eleven_multilingual_v2', label: 'Multilingual v2' },
  { value: 'eleven_turbo_v2_5', label: 'Turbo v2.5' },
  { value: 'eleven_flash_v2_5', label: 'Flash v2.5' },
] as const;

export async function renderSettingsView(root: HTMLElement): Promise<void> {
  const settings: AppSettings = await invoke('get_settings');

  root.innerHTML = `
    <div class="settings-view">
      <div class="settings-header">
        <h2>Settings</h2>
        <button id="btn-back" class="btn btn-icon" title="Back to editor">&#8592;</button>
      </div>

      <form id="settings-form" class="settings-form" autocomplete="off">

        <div class="form-group">
          <label for="api-key">ElevenLabs API Key</label>
          <input
            type="password"
            id="api-key"
            name="api-key"
            class="input-full"
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            value="${escHtml(settings.elevenlabsApiKey)}"
            autocomplete="new-password">
          <small>Stored in the app config directory — never sent anywhere except ElevenLabs.</small>
        </div>

        <div class="form-group">
          <label for="output-dir">Output Directory</label>
          <div class="dir-picker-row">
            <input
              type="text"
              id="output-dir"
              name="output-dir"
              class="input-full"
              readonly
              placeholder="Choose a directory…"
              value="${escHtml(settings.outputDir)}">
            <button type="button" id="btn-browse" class="btn btn-secondary">Browse…</button>
          </div>
          <small>Generated audio files (.mp3) are saved here.</small>
        </div>

        <div class="form-group">
          <label for="default-model">Default TTS Model</label>
          <select id="default-model" name="default-model" class="select-full">
            ${TTS_MODELS.map(
              (m) =>
                `<option value="${m.value}" ${settings.defaultTtsModel === m.value ? 'selected' : ''}>${m.label}</option>`
            ).join('')}
          </select>
          <small>Applied to new sections when no model is specified in the manifest.</small>
        </div>

        <div class="form-actions">
          <button type="submit" class="btn btn-primary">Save</button>
          <button type="button" id="btn-cancel" class="btn btn-secondary">Cancel</button>
        </div>

      </form>
    </div>`;

  // Back / cancel navigation
  root.querySelector('#btn-back')!.addEventListener('click', () => navigate('manifest'));
  root.querySelector('#btn-cancel')!.addEventListener('click', () => navigate('manifest'));

  // Directory picker
  root.querySelector('#btn-browse')!.addEventListener('click', async () => {
    const selected = await open({
      directory: true,
      multiple: false,
      title: 'Select Output Directory',
    });
    if (typeof selected === 'string') {
      (root.querySelector('#output-dir') as HTMLInputElement).value = selected;
    }
  });

  // Form submit
  root.querySelector('#settings-form')!.addEventListener('submit', async (e) => {
    e.preventDefault();
    const apiKey = (root.querySelector('#api-key') as HTMLInputElement).value.trim();
    const outputDir = (root.querySelector('#output-dir') as HTMLInputElement).value.trim();
    const model = (root.querySelector('#default-model') as HTMLSelectElement).value;

    try {
      await invoke('save_settings', {
        elevenlabsApiKey: apiKey,
        outputDir,
        defaultTtsModel: model,
      });
      navigate('manifest');
    } catch (err) {
      alert(`Failed to save settings: ${err}`);
    }
  });
}

