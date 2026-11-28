//! Slide-note tags that drive signage screens (#208). The ProPresenter module names `signage` in its `uses`.
//!
//! Which action does what (a value is `Name:Thing` for the `-screen` and `-group` forms; the plain forms act on every screen):
//!
//! ```text
//! signage-screen=Lobby TV:Welcome        a screen plays the signage playlist "Welcome"
//! signage-group=Lobby:Welcome            every screen in a group plays it
//! signage-all=Welcome                    every screen plays it
//! signage-push[-screen|-group]=...       send one picture to play once, then the screen goes back to its playlist
//! signage-captions[-screen|-group]       show Relay's live captions until a resume tag
//! signage-service[-screen|-group]        show the Planning Center service rundown until a resume tag
//! signage-resume[-screen|-group]         end what tags put on the screens: playlists, pushes and live views
//! ```

/// Every signage action, with whether it takes a value (added to the actions the tags read, whether or not signage is on).
pub fn names() -> Vec<(String, bool)> {
    let mut out: Vec<(String, bool)> = ["signage-screen", "signage-group", "signage-all"].into_iter().map(|n| (n.to_owned(), true)).collect();
    for suffix in ["-screen", "-group", ""] {
        for kind in ["push", "captions", "service", "resume"] {
            out.push((format!("signage-{kind}{suffix}"), !suffix.is_empty() || kind == "push"));
        }
    }
    out
}
