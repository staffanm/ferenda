
var base = "";
var termDataUrl = base + "/var/terms";
var commonDataUrl = base + "/var/common";
var statisticsUrl = null;

var sharedData = {
  terms: {},
  common: {}
};

/* main */

$(function () {

  loadSharedData();

  statisticsUrl = $('#queryForm').attr('action') + ";stats"

  $('#queryForm').submit(function () {
    var $self = $(this);
    var serviceRef = $self.attr('action') +'?'+ $self.serialize();
    window.location.hash = serviceRef;
    queryService(serviceRef);
    return false;
  });

  $('#resultsView a[href], a.svc, a.sort').live('click', function () {
    var serviceRef = $(this).attr('href').substring(1);
    if (serviceRef) {
      window.location.hash = serviceRef;
      queryService(serviceRef);
    }
    return false;
  });

});

function loadSharedData() {
  $.getJSON(termDataUrl, function (data) {
    $.each(data.topic, function () {
      sharedData.terms[this.iri] = this;
    });

    $.getJSON(commonDataUrl, function (data) {
      $.each(data.topic, function () {
        sharedData.common[this.iri] = this;
      });
      loadStats();
      var serviceRef = window.location.hash.substring(1);
      queryService(serviceRef);
    });

  });
}

/* lookup */

function queryService(serviceRef) {
  if (!serviceRef) {
    return;
  }
  loader.start($('#content'));
  $.getJSON(base + serviceRef, function (data) {
    loader.stop($('#content'));
    (data.itemsPerPage? renderResults : renderDocument)(serviceRef, data);
  }).error(function (response) {
    loader.stop($('#content'));
    renderError(serviceRef, response);
  });
}

function loadStats() {
  loader.start($('#queryBox'));
  $.getJSON(statisticsUrl, function (stats) {
    loader.stop($('#queryBox'));
    renderStats(stats);
  });
}

/* render */

function renderStats(stats, dynamicSelects) {
  var $optFields = $('#optFields').empty();
  $.each(stats.slices, function () {
    var $select = $('#' + this.dimension);
    if (!this.observations) return;
    if (!$select[0]) {
      if (!dynamicSelects) return;
      var $selectBox = $('#selectTemplate').tmpl({
        id: this.dimension,
        label: termLabel(this.dimension),
        name: ((this.observations[0].year)? 'year-' : '') +
              this.dimension +
              ((this.observations[0].ref)? '.iri' : '')
      });
      $optFields.append($selectBox);
      $select = $('select', $selectBox);
    } else {
      if (dynamicSelects) {
        $select.empty().addClass('narrowed');
      } else if ($select.is('.narrowed')) {
        $select.empty().removeClass('narrowed');
      } else if ($select.is(':has(option)')) {
        return; // .. or update if changed?
      }
    }
    $.each(this.observations, function () {
      var value, label;
       if (this.year) {
        label = this.year;
        value = this.year;
      } else if (this.ref) {
        var obj = sharedData.common[this.ref];
        if (!obj) {
          console.log("Unknown object: " + this.ref);
          var leaf = this.ref.substring(this.ref.lastIndexOf('/') + 1);
        }
        label = obj? obj.name || obj.altLabel : leaf;
        value = '*/' + this.ref.substring(this.ref.lastIndexOf('/') + 1);
      } else if (this.term) {
        if (!sharedData.terms[this.term]) {
          console.log("Unknown term: " + this.term);
        }
        label = sharedData.terms[this.term].label;
        value = this.term;
      }
      if (!value)
        return;
      $select.append('<option value="'+ value +'">'+ label +' ('+ this.count +')'+'</option>');
    });
  });
}

function renderResults(serviceRef, results) {
  var endIndex = results.startIndex + results.itemsPerPage;
  if (endIndex > results.totalResults) {
    endIndex = results.totalResults;
  }
  $('#errorInfo').empty();
  $('#documentView').empty();
  $('#resultsTemplate').tmpl({
    queryStr: serviceRef,
    start: results.startIndex + 1,
    end: endIndex,
    totalResults: results.totalResults,
    results: results
  }).appendTo($('#resultsView').removeClass('folded').empty());
  if (results.statistics) {
    renderStats(results.statistics, true);
  } else {
    loadStats();
  }
}

function renderDocument(serviceRef, doc) {
  $('#errorInfo').empty();
  $('#resultsView:has(*)').addClass('folded');

  var props = {}, rels = {}, revs = {};
  for (key in doc) {
    var value = doc[key];
    if (key === '@context')
      continue;
    if (key === 'rev') {
      for (revKey in value) {
        revs['rev.' + revKey + ''] = value[revKey];
      }
    } else {
      if (typeof value === 'string' ||
          value.length !== undefined && typeof value[0] === 'string') {
        props[key] = value;
      } else {
        rels[key] = value;
      }
    }
  }

  $('#documentTemplate').tmpl({
    heading: doc.identifier || doc.name || doc.altLabel || doc.label,
    properties: props,
    relations: rels,
    incoming: revs
  }).appendTo($('#documentView').empty());
}

function renderError(serviceRef, response) {
  $('#documentView').empty();
  $('#errorTemplate').tmpl({
    serviceRef: serviceRef,
    response: response
  }).appendTo($('#errorInfo').empty());
}

/* utils */

function termLabel(key) {
  if (key.slice(0,4) === "rev.") {
    var obj = sharedData.terms[key.slice(4)];
    return (obj && obj.inverseOf)? obj.inverseOf.label : key;
  }
  var obj = sharedData.terms[key];
  return obj? obj.label : key;
}

function toServiceRef(iri) {
  return iri.replace(/^https?:\/\/[^\/]+([^#]+?)(\/data\.json)?(#.+)?$/, "$1/data.json$3");
}

function sortLink(serviceRef, sortTerm) {
  var match = serviceRef.match(/\&_sort=([^&]+)/);
  var currSort = match? match[1] : "";
  var doSort = sortTerm;
  if (currSort === '-'+sortTerm) {
    doSort = "";
  } else if (currSort === sortTerm) {
    doSort = '-' + sortTerm;
  }
  return serviceRef.replace(/\&_sort=[^&]+/, "") +
      (doSort? '&_sort=' + doSort : "");
}

/**
 * Throbbing loader indicator.
 */
var loader = new function () {

  var dur = this.dur = 1000;

  this.start = function (o) {
    return fadeOut.apply(o.addClass('loading')[0]);
  }
  this.stop = function (o) {
    return o.stop(true).removeClass('loading').css('opacity', 1);
  }

  function fadeOut() {
    $(this).animate({'opacity': 0.2}, {queue: true, duration: dur, complete: fadeIn});
  }
  function fadeIn() {
    $(this).animate({'opacity': 1}, {queue: true, duration: dur, complete: fadeOut});
  }

};

