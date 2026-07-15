/* Autocompletar de direcciones + mapa (Leaflet). Mejora progresiva:
   sin JavaScript el formulario sigue funcionando (geocodifica el texto). */
(function () {
  'use strict';

  var MADRID = [40.4168, -3.7038];

  function crearMapa(idElemento) {
    if (typeof L === 'undefined') return null;
    var el = document.getElementById(idElemento);
    if (!el) return null;
    var mapa = L.map(idElemento, { zoomControl: true }).setView(MADRID, 12);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(mapa);
    return mapa;
  }

  function icono(color) {
    return L.divIcon({
      className: '',
      html: '<div style="width:16px;height:16px;border-radius:' +
        (color === 'destino' ? '3px' : '50%') +
        ';background:#09090b;border:3px solid #fff;box-shadow:0 1px 6px rgba(0,0,0,.4)"></div>',
      iconSize: [16, 16],
      iconAnchor: [8, 8],
    });
  }

  /* ---- Página del formulario ---- */
  function initFormulario(geocodeUrl) {
    var mapa = crearMapa('mapa');
    var marcadores = { origen: null, destino: null };

    function pintar(campo, lat, lng) {
      if (!mapa) return;
      if (marcadores[campo]) mapa.removeLayer(marcadores[campo]);
      marcadores[campo] = L.marker([lat, lng], { icon: icono(campo) }).addTo(mapa);
      var puntos = Object.values(marcadores).filter(Boolean)
        .map(function (m) { return m.getLatLng(); });
      if (puntos.length === 2) {
        mapa.fitBounds(L.latLngBounds(puntos), { padding: [50, 50] });
      } else {
        mapa.setView(puntos[0], 14);
      }
    }

    ['origen', 'destino'].forEach(function (campo) {
      var input = document.getElementById(campo);
      var lista = document.getElementById(campo + '-sugerencias');
      var latEl = document.getElementById(campo + '_lat');
      var lngEl = document.getElementById(campo + '_lng');
      if (!input || !lista) return;

      var temporizador = null;

      function limpiar() { lista.innerHTML = ''; }

      function buscar() {
        var q = input.value.trim();
        if (q.length < 3) { limpiar(); return; }
        fetch(geocodeUrl + '?q=' + encodeURIComponent(q))
          .then(function (r) { return r.json(); })
          .then(function (d) {
            limpiar();
            (d.opciones || []).forEach(function (op) {
              var li = document.createElement('li');
              li.textContent = op.texto;
              li.setAttribute('role', 'option');
              li.addEventListener('mousedown', function (ev) {
                ev.preventDefault();
                input.value = op.texto;
                latEl.value = op.lat;
                lngEl.value = op.lng;
                limpiar();
                pintar(campo, op.lat, op.lng);
              });
              lista.appendChild(li);
            });
          })
          .catch(limpiar);
      }

      input.addEventListener('input', function () {
        latEl.value = '';           // el texto cambió: la selección ya no vale
        lngEl.value = '';
        clearTimeout(temporizador);
        temporizador = setTimeout(buscar, 450);
      });
      input.addEventListener('blur', function () { setTimeout(limpiar, 150); });
    });
  }

  /* ---- Página de la oferta (ruta pintada) ---- */
  function initOferta() {
    var datos = document.getElementById('datos-ruta');
    if (!datos) return;
    var info = JSON.parse(datos.textContent);
    var mapa = crearMapa('mapa');
    if (!mapa) return;

    L.marker([info.origen[0], info.origen[1]], { icon: icono('origen') }).addTo(mapa);
    L.marker([info.destino[0], info.destino[1]], { icon: icono('destino') }).addTo(mapa);

    var limites;
    if (info.ruta && info.ruta.length > 1) {
      var linea = L.polyline(info.ruta, { color: '#09090b', weight: 5, opacity: .85 }).addTo(mapa);
      limites = linea.getBounds();
    } else {
      limites = L.latLngBounds([info.origen, info.destino]);
    }
    mapa.fitBounds(limites, { padding: [50, 50] });
  }

  window.TaxiReserva = { initFormulario: initFormulario, initOferta: initOferta };
})();
