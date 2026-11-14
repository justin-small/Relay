# Opening the operator panel {#operator-access}

The operator panel is where you control Relay. Its address is the Relay
computer's LAN IP address with `/admin` on the end, over `https://`. For
example: `https://192.168.1.50/admin`.

On the Relay computer, the Relay app opens the panel in its own window: choose
**Open panel** from the Relay menu. You can also open it in a browser from any
computer or tablet on the same network. The **Viewer links** page shows the
address.

## The certificate warning

The first time you open the panel, the browser shows a warning page instead
of the panel. **This is expected.** Here is why, and how to check that it is
safe.

### Why the warning appears

`https://` addresses use a **certificate**: a small file that proves to the
browser which computer it is talking to. Public websites buy theirs from a
company the browser already trusts. A computer on a venue network cannot do
that, so Relay makes its own certificate the first time it starts. This is called a
**self-signed** certificate. The browser has never seen it before, so it
warns you.

The warning protects you from a stranger's computer pretending to be yours.
The way to rule that out is to compare the certificate's **fingerprint**. A
fingerprint is a long code, unique to one certificate, like
`D0:63:EE:F2:04:8C:…`. On the Relay computer, choose **Show certificate
fingerprint** from the Relay menu; the **Viewer links** page in the panel
shows it too. If the browser shows the same code, you are talking to your own
Relay computer. (The Relay app's own window checks it for you and never
warns.)

::: warning
Compare the fingerprint before you click through, at least the first time on
each browser. If it does not match, **do not continue.** Something else is
answering at that address. Close the page and check that you typed the right
address and are on the right network.
:::

::: note
Relay makes a new certificate, with a new fingerprint, when the computer's
IP address changes (for example at a new venue) or when the certificate is
close to expiring. Always compare with what **Show certificate fingerprint**
shows today, not an old note.
:::

### Chrome

![Chrome's certificate warning, with the details open.](images/cert-warning-chrome.png){width=85%}

1. The page says **Your connection is not private**. Click **Advanced** (1).
   Its label changes to **Hide advanced**.
2. To see the fingerprint, click **Not secure** at the left of the address
   bar, then **Certificate is not valid**. The certificate viewer opens.
3. On the **General** tab, find **Fingerprints**, then **SHA-256
   Fingerprint**. Compare it with the one **Show certificate fingerprint**
   shows on the Relay computer.
4. The browser shows the code in pairs separated by spaces; Relay prints it
   with colons. Ignore the spaces and colons, and compare the letters and
   numbers. Checking the first eight and the last eight characters is enough.
5. If it matches, close the viewer and click **Proceed to 192.168.1.50
   (unsafe)** (2). In the picture it says `localhost`, because the test copy
   ran on the same computer.
6. The **Operator panel** sign-in page opens.

Chrome remembers your choice for a while. You see the warning again after a
browser restart, or when the certificate changes.

### Microsoft Edge

Edge works the same way as Chrome.

1. The page says **Your connection isn't private**. Click **Advanced**.
2. Click **Not secure** in the address bar, then **Your connection to this
   site isn't secure**, then the certificate button (**Show certificate**).
3. On the **General** tab, compare the **SHA-256 Fingerprint**.
4. If it matches, close the viewer and click **Continue to 192.168.1.50
   (unsafe)**.

### Safari (Mac, iPad)

1. The page says **This Connection Is Not Private**. Click **Show Details**.
2. Click **view the certificate**.
3. Open **Details** and scroll down to **Fingerprints**. Compare the
   **SHA-256** line.
4. If it matches, click **OK**, then **visit this website**, then **Visit
   Website** to confirm.
5. Your Mac may ask for your password or Touch ID. This lets Safari remember
   that you trust this certificate.

### Firefox

1. The page says **Warning: Potential Security Risk Ahead**. Click
   **Advanced…**.
2. Click **View Certificate**. A new tab opens.
3. Under **Fingerprints**, compare **SHA-256**.
4. Close that tab. Back on the warning, click **Accept the Risk and
   Continue**.

## Signing in

After the warning, you see either the first-run page or the **Operator panel**
sign-in page.

### The first time: create the Owner

On a new Relay nobody has an account yet, so the page is **Set up Relay**.

This page opens only on the Relay computer itself until the Owner exists (a
service install on Linux asks for its one-time setup code instead, so it can
be done from another device). If you see **Open this page in a browser on the
computer Relay runs on**, do that.

1. If the page asks for a **Setup code** (a service install) or an **Admin
   token** (just after an upgrade from Relay 2.0), type it.
2. Type a **Username**, **Your name**, and a **Password** twice. The password
   must have at least 8 characters.
3. Click **Create the Owner**.

You are signed in as the Owner. The setup page never appears again. Add the other people on the **Users** page, see
[Users](#users).

### Every other time

![The sign-in page.](images/operator-signin.png){width=55%}

1. Type your **Username** and **Password**.
2. Click **Sign in**, or press `Return` / `Enter`.

If an Admin gave you a temporary password, Relay asks you to choose your own
before it opens the panel.

On a trusted device (such as the sound booth computer) the page may ask for
your name and PIN instead. **Use my password instead** goes back to the
password form.

You should now see the operator panel. See [The operator panel](#admin-panel).

### If the sign-in is wrong

![The sign-in page after a wrong password.](images/operator-signin-error.png){width=55%}

1. **Wrong username or password.** means one of them did not match. Type them
   again. The password is case-sensitive: `Relay` and `relay` are different.
   Relay does not say which one was wrong.

After five wrong tries in a row, that computer is locked out for five minutes
and the page says **Too many attempts. Try again in … min.** with the number of
minutes left. Wait, then try
again.

If you have forgotten your password, ask an Admin to reset it. If you are the
only Owner, see [If nobody can sign in](#lockout).

### How long you stay signed in

- A password sign-in lasts 30 days on that browser, and a PIN sign-in lasts
  12 hours.
- Restarting Relay does not sign anyone out.
- Changing your password, or an Admin resetting it, signs you out of your other
  browsers. Turning off or deleting an account signs that person out at once.
- **Sign out**, at the top right of the panel, signs out that browser now.

::: warning
Your password is what protects the panel, and the panel spends money on your
OpenAI account. Choose a real password, not a word. Do not write it on the
screen or share it in a group chat. Every person should have their own account.
:::
