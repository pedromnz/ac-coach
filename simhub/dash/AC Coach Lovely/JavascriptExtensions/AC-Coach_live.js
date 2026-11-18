// AC Coach - le os dados que o app do Assetto Corsa grava em live.txt
// (o texto chega na variavel do dash 'acCoach', uma linha "chave=valor" por item)
function acc(key, def) {
    var t = $prop('variable.acCoach');
    if (t == null || t === '') return def;
    var s = '\n' + t;
    var k = '\n' + key + '=';
    var i = s.indexOf(k);
    if (i < 0) return def;
    var j = s.indexOf('\n', i + k.length);
    return s.substring(i + k.length, j < 0 ? s.length : j);
}
function acc_num(key, def) {
    var v = parseFloat(acc(key, ''));
    return isNaN(v) ? def : v;
}
// app ligado = arquivo atualizado nos ultimos 3 s
function acc_on() {
    var ts = acc_num('ts', 0);
    return ts > 0 && Math.abs(Date.now() / 1000 - ts) < 3;
}
function acc_low() {
    return acc_on() && acc('low', '0') == '1';
}
