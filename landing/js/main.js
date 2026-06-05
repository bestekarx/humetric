document.getElementById('register-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('email').value;
    const password = document.getElementById('password').value;
    const msg = document.getElementById('register-message');

    try {
        const resp = await fetch('/v1/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password, captcha_token: 'landing-page' }),
        });
        const data = await resp.json();
        if (resp.ok) {
            msg.textContent = data.message || 'Dogrulama emaili gonderildi!';
            msg.style.color = 'green';
        } else {
            msg.textContent = data.error?.message || 'Kayit basarisiz';
            msg.style.color = 'red';
        }
    } catch (err) {
        msg.textContent = 'Baglanti hatasi';
        msg.style.color = 'red';
    }
});
