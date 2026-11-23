//! Source and target languages (`app/languages.py`): the same names in the same order, since config.json stores a target by name
//! and the order is the order a viewer lists them in.

pub struct Language {
    pub name: &'static str,
    pub label: &'static str,
    pub iso: &'static str,
    pub script: &'static str,
    /// Only the text translation engine can produce it (the touch panels group these apart).
    pub text_only: bool,
}

const fn lang(name: &'static str, label: &'static str, iso: &'static str, script: &'static str, text_only: bool) -> Language {
    Language { name, label, iso, script, text_only }
}

/// Relay hears one source language.
pub const SOURCE: Language = lang("ENGLISH", "English", "en", "latin", false);

pub const SOURCES: [&str; 1] = ["ENGLISH"];

pub const TARGETS: [Language; 17] = [
    lang("SPANISH", "Spanish", "es", "latin", false),
    lang("PORTUGUESE", "Portuguese", "pt", "latin", false),
    lang("FRENCH", "French", "fr", "latin", false),
    lang("GERMAN", "German", "de", "latin", false),
    lang("ITALIAN", "Italian", "it", "latin", false),
    lang("RUSSIAN", "Russian", "ru", "cyrillic", false),
    lang("CHINESE", "Chinese", "zh", "han", false),
    lang("JAPANESE", "Japanese", "ja", "kana", false),
    lang("KOREAN", "Korean", "ko", "hangul", false),
    lang("HINDI", "Hindi", "hi", "devanagari", false),
    lang("INDONESIAN", "Indonesian", "id", "latin", false),
    lang("VIETNAMESE", "Vietnamese", "vi", "latin", false),
    lang("ENGLISH", "English", "en", "latin", false),
    lang("ARABIC", "Arabic", "ar", "arabic", true),
    lang("NEPALI", "Nepali", "ne", "devanagari", true),
    lang("TELUGU", "Telugu", "te", "telugu", true),
    // No ISO 639-1 code; hmn is the ISO 639-2 code for the Hmong group.
    lang("HMONG", "Hmong", "hmn", "latin", true),
];

pub fn is_source(name: &str) -> bool {
    SOURCES.contains(&name)
}

pub fn is_target(name: &str) -> bool {
    TARGETS.iter().any(|t| t.name == name)
}

/// Every target the operator may enable, minus the source language (translating English into English would open a billed
/// session to no effect).
pub fn target_names(source: &str) -> Vec<&'static str> {
    let source = source.to_uppercase();
    TARGETS.iter().map(|t| t.name).filter(|n| *n != source).collect()
}

pub fn target(name: &str) -> Option<&'static Language> {
    TARGETS.iter().find(|t| t.name == name)
}
