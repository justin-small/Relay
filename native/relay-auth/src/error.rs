//! Why a call was refused. `Refusal::message` is the only text that may reach a browser (app/users.py `MESSAGES`, word for
//! word), and the status per key is `_REFUSAL_STATUS` in app/main.py. A database failure is `Error::Unavailable`; its cause
//! goes to the log only.

/// A rule said no.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Refusal {
    BadLogin,
    Disabled,
    BadUsername,
    BadName,
    BadPassword,
    BadPin,
    BadRole,
    BadDeviceName,
    UsernameTaken,
    NotFound,
    RoleTooHigh,
    AccountAboveYou,
    LastOwner,
    SelfAction,
    WrongPassword,
    SamePassword,
    WrongPin,
    PinCleared,
    NoDevice,
    NotAllowed,
    BadTokenName,
    BadExpiry,
    TooManyTokens,
}

impl Refusal {
    /// The key Python uses (`Refused.key`), for the tests and the log.
    pub const fn key(self) -> &'static str {
        match self {
            Self::BadLogin => "bad_login",
            Self::Disabled => "disabled",
            Self::BadUsername => "bad_username",
            Self::BadName => "bad_name",
            Self::BadPassword => "bad_password",
            Self::BadPin => "bad_pin",
            Self::BadRole => "bad_role",
            Self::BadDeviceName => "bad_device_name",
            Self::UsernameTaken => "username_taken",
            Self::NotFound => "not_found",
            Self::RoleTooHigh => "role_too_high",
            Self::AccountAboveYou => "account_above_you",
            Self::LastOwner => "last_owner",
            Self::SelfAction => "self_action",
            Self::WrongPassword => "wrong_password",
            Self::SamePassword => "same_password",
            Self::WrongPin => "wrong_pin",
            Self::PinCleared => "pin_cleared",
            Self::NoDevice => "no_device",
            Self::NotAllowed => "not_allowed",
            Self::BadTokenName => "bad_token_name",
            Self::BadExpiry => "bad_expiry",
            Self::TooManyTokens => "too_many_tokens",
        }
    }

    pub const fn message(self) -> &'static str {
        match self {
            Self::BadLogin => "Wrong username or password.",
            Self::Disabled => "This account is turned off. Ask an admin to turn it back on.",
            Self::BadUsername => "A username is 3 to 32 characters: letters, digits, dot, dash or underscore.",
            Self::BadName => "A name is 1 to 80 characters.",
            Self::BadPassword => "A password is 8 to 200 characters.",
            Self::BadPin => "A PIN is 4 to 8 digits.",
            Self::BadRole => "That is not a role.",
            Self::BadDeviceName => "A device name is 1 to 80 characters.",
            Self::UsernameTaken => "That username is already taken.",
            Self::NotFound => "That does not exist any more.",
            Self::RoleTooHigh => "You cannot give a role higher than your own.",
            Self::AccountAboveYou => "You cannot change an account with a higher role than yours.",
            Self::LastOwner => "There must always be one active owner.",
            Self::SelfAction => "You cannot turn off or delete your own account.",
            Self::WrongPassword => "The password is wrong.",
            Self::SamePassword => "Choose a different password from the current one.",
            Self::WrongPin => "That PIN is not right.",
            Self::PinCleared => "Too many wrong PINs. Sign in with your password and set a new PIN.",
            Self::NoDevice => "This browser is not a trusted device.",
            Self::NotAllowed => "Not allowed",
            Self::BadTokenName => "A token name is 1 to 80 characters.",
            Self::BadExpiry => "A token expires after 30, 90 or 365 days, or never.",
            Self::TooManyTokens => "You have the most tokens allowed. Revoke one you no longer use first.",
        }
    }

    /// The HTTP status (`_REFUSAL_STATUS`; anything not listed there is 400).
    pub const fn status(self) -> u16 {
        match self {
            Self::BadLogin | Self::WrongPin | Self::PinCleared | Self::NoDevice => 401,
            Self::Disabled | Self::NotAllowed | Self::RoleTooHigh | Self::AccountAboveYou | Self::SelfAction
            | Self::WrongPassword => 403,
            Self::NotFound => 404,
            Self::UsernameTaken | Self::LastOwner => 409,
            _ => 400,
        }
    }
}

/// Everything a call can fail with.
#[derive(Debug)]
pub enum Error {
    Refused(Refusal),
    /// The database is down, unreadable, or from a Relay too old or new to share. The cause is only in the log.
    Unavailable,
}

impl From<Refusal> for Error {
    fn from(r: Refusal) -> Self {
        Error::Refused(r)
    }
}

impl From<sqlx::Error> for Error {
    fn from(e: sqlx::Error) -> Self {
        tracing::warn!("database error: {e}");
        Error::Unavailable
    }
}

pub type Result<T> = std::result::Result<T, Error>;
