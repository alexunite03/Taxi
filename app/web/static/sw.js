/* Service worker mínimo: recibe el push y muestra la notificación.
   La reserva nunca depende de esto (plan §5). */
self.addEventListener('push', function (e) {
  var datos = {};
  try { datos = e.data ? e.data.json() : {}; } catch (_) {}
  e.waitUntil(
    self.registration.showNotification(datos.titulo || 'Tu reserva de taxi', {
      body: datos.cuerpo || '',
      data: { url: datos.url || '/' },
      icon: undefined,
    })
  );
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(clients.openWindow(url));
});
