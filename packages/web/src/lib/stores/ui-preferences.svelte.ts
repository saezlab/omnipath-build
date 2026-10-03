import { browser } from '$app/environment';

const UI_PREFERENCES_STORAGE_KEY = 'omnipath-ui-preferences';

type UiPreferencesValue = {
  showExplanations: boolean;
};

function readUiPreferences(): UiPreferencesValue {
  if (!browser) return { showExplanations: false };
  try {
    const parsed = JSON.parse(
      localStorage.getItem(UI_PREFERENCES_STORAGE_KEY) || '{}',
    ) as Partial<UiPreferencesValue>;
    return {
      showExplanations: parsed.showExplanations === true,
    };
  } catch {
    return { showExplanations: false };
  }
}

let uiPreferences = $state<UiPreferencesValue>(readUiPreferences());

function writeUiPreferences(next: UiPreferencesValue) {
  if (!browser) return;
  localStorage.setItem(UI_PREFERENCES_STORAGE_KEY, JSON.stringify(next));
}

export function getUiPreferences() {
  return {
    get showExplanations() {
      return uiPreferences.showExplanations;
    },
    setShowExplanations(showExplanations: boolean) {
      uiPreferences = { ...uiPreferences, showExplanations };
      writeUiPreferences(uiPreferences);
    },
  };
}
