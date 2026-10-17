# Security

Ghost-Vision is a sensing system, so its own security matters. This page lists the **known limitations** openly and explains how to report a new problem.

## Known limitations

These are known, documented, and on the list to improve. If you deploy Ghost-Vision, plan around them.

1. **Measurements travel unencrypted inside the sensing network.** The boards send their data to the console over UDP without encryption or authentication. Someone who has joined the sensing WiFi could, in principle, send fake data — either to trigger a false alarm or to hide a real one.
   **What protects you today:** the sensing network uses WPA2, so joining it requires the password. Use a strong password, keep it private, and don't connect the sensing router to the internet or to any other network.

2. **The WiFi password is stored in the firmware settings file.** After you follow the guide, `firmware/sdkconfig` contains your sensing network's password in plain text.
   **What to do:** never upload or share your copy of that file. Put the `YOUR_SSID` / `YOUR_PASSWORD` placeholders back before sharing your project.

3. **The network can be seen.** Anyone with a WiFi scanner can see the sensing network's name and the four connected boards, just like any WiFi network. They can't tell it's a sensor, but they can see it exists.
   **What to do:** give the network a plain name that doesn't identify you or what it's for.

4. **Jamming.** Like every wireless device, the boards can be disrupted by deliberate radio interference. The console reports nodes that go quiet, but it doesn't yet raise an alarm specifically for interference.

## Reporting a security problem

If you find a security issue **that isn't listed above**, please report it **privately** first, so it can be fixed before it's public:

- Use the **Security** tab → **Report a vulnerability** on the GitHub repository page.

Please include what you found, how to reproduce it, and what effect it could have. You'll get a reply as soon as possible. Thank you for helping keep people safe.

For everything else — including ideas for fixing the limitations above — a normal issue or pull request is perfect.
